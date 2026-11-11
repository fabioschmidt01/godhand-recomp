"""Etapas do pipeline ISO -> C++ -> exe. Usado igualmente pela CLI e pela GUI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Env
from .iso9660 import IsoReader
from .mods import load_mods


class StageError(RuntimeError):
    pass


class Reporter:
    """Recebe eventos do pipeline. A CLI imprime; a GUI converte em sinais Qt."""

    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def stage(self, key: str, status: str, detail: str = "") -> None:
        self.log(f"[{key}] {status}{': ' + detail if detail else ''}")

    def progress(self, key: str, fraction: float) -> None:
        pass


@dataclass
class Context:
    env: Env
    iso: Path
    reporter: Reporter
    force: set[str] = field(default_factory=set)
    game: dict = field(default_factory=dict)
    effective_iso: Path | None = None
    cancelled: bool = False
    current_proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        """Pede a interrupção: mata o processo externo em execução (e seus filhos) e aborta a etapa."""
        self.cancelled = True
        p = self.current_proc
        if p is not None and p.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                p.kill()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise StageError("cancelado pelo usuário")

    @property
    def workdir(self) -> Path:
        return self.env.work / self.game["serial"]

    @property
    def gen(self) -> Path:
        return self.workdir / "gen"

    @property
    def elf(self) -> Path:
        return self.workdir / "elf" / self.game["elf"]

    def state(self) -> dict:
        p = self.workdir / "state.json"
        return json.loads(p.read_text("utf-8")) if p.is_file() else {}

    def is_cached(self, stage: str, key: str) -> bool:
        return stage not in self.force and self.state().get(stage) == key

    def mark(self, stage: str, key: str) -> None:
        s = self.state()
        s[stage] = key
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "state.json").write_text(json.dumps(s, indent=2), "utf-8")


# ---------------------------------------------------------------- utilidades

def sha1_file(path: Path, on_fraction: Callable[[float], None] | None = None) -> str:
    h = hashlib.sha1()
    total = max(path.stat().st_size, 1)
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
            done += len(chunk)
            if on_fraction:
                on_fraction(done / total)
    return h.hexdigest()


def cached_sha1(env: Env, path: Path, on_fraction: Callable[[float], None] | None = None) -> str:
    """SHA-1 com cache por (caminho, tamanho, mtime): a ISO tem 1,5 GB."""
    cache_file = env.work / "hash-cache.json"
    cache = json.loads(cache_file.read_text("utf-8")) if cache_file.is_file() else {}
    st = path.stat()
    ident = f"{path}|{st.st_size}|{st.st_mtime_ns}"
    if ident not in cache:
        cache[ident] = sha1_file(path, on_fraction)
        env.work.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, indent=2), "utf-8")
    return cache[ident]


def key_of(*parts: object) -> str:
    return hashlib.sha1(json.dumps([str(p) for p in parts]).encode()).hexdigest()


def pcsx2_crc(elf: bytes) -> str:
    """CRC que o PCSX2 usa para nomear .pnach: XOR de todas as palavras de 32 bits do ELF."""
    n = len(elf) // 4
    crc = 0
    for (w,) in struct.iter_unpack("<I", elf[: n * 4]):
        crc ^= w
    return f"{crc:08X}"


def tool_env(env: Env) -> dict:
    e = dict(os.environ)
    extra = [d for d in (env.java_home / "bin", env.mingw_bin) if d.is_dir()]  # só o que está configurado
    if (env.java_home / "bin").is_dir():
        e["JAVA_HOME"] = str(env.java_home)
    e["PATH"] = os.pathsep.join([*map(str, extra), e.get("PATH", "")])
    e["CCACHE_DIR"] = str(env.ccache_dir)
    return e


def run_tool(ctx: Context, stage: str, cmd: list[str], cwd: Path | None = None) -> None:
    """Executa um processo, enviando a saída para o log da interface e para work/logs/<stage>.log."""
    logs = ctx.env.work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ctx.reporter.log("> " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with open(logs / f"{stage}.log", "w", encoding="utf-8", errors="replace") as logf:
        p = subprocess.Popen(
            cmd, cwd=cwd, env=tool_env(ctx.env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
        ctx.current_proc = p
        assert p.stdout is not None
        for line in p.stdout:
            logf.write(line)
            ctx.reporter.log(line.rstrip())
        code = p.wait()
        ctx.current_proc = None
    ctx.check_cancelled()
    if code != 0:
        raise StageError(f"{Path(cmd[0]).name} terminou com código {code} (log: {logs / (stage + '.log')})")


# ---------------------------------------------------------------- etapas

def ingest(ctx: Context) -> str:
    if not ctx.iso.is_file():
        raise StageError(f"ISO não encontrada: {ctx.iso}")
    with IsoReader(ctx.iso) as iso:
        cnf = iso.read("SYSTEM.CNF").decode("ascii", "replace")
        m = re.search(r"BOOT2\s*=\s*cdrom0:\\?([^;\s]+)", cnf, re.I)
        if not m:
            raise StageError("SYSTEM.CNF sem BOOT2: não parece um disco de PS2")
        elf_name = m.group(1)
        elf = iso.read(elf_name)
    if elf[:4] != b"\x7fELF":
        raise StageError(f"{elf_name} não é um ELF")
    ctx.reporter.log("Calculando SHA-1 da ISO (só na primeira vez)...")
    sha1 = cached_sha1(ctx.env, ctx.iso, lambda f: ctx.reporter.progress("ingest", f))
    ctx.game = {
        "serial": elf_name.replace("_", "-").replace(".", ""),
        "elf": elf_name,
        "entry": f"0x{struct.unpack_from('<I', elf, 24)[0]:08X}",
        "elf_crc_pcsx2": pcsx2_crc(elf),
        "elf_sha1": hashlib.sha1(elf).hexdigest(),
        "iso_sha1": sha1,
        "iso": str(ctx.iso),
    }
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    (ctx.workdir / "game.json").write_text(json.dumps(ctx.game, indent=2), "utf-8")
    g = ctx.game
    return f"{g['serial']} | ELF {g['elf']} | entry {g['entry']} | CRC {g['elf_crc_pcsx2']}"


def patch_iso(ctx: Context) -> str:
    patches = [m for m in load_mods(ctx.env.mods) if m.enabled and m.type == "xdelta"]
    if not patches:
        ctx.effective_iso = ctx.iso
        return "nenhum patch xdelta ativo; usando a ISO original"
    for m in patches:
        if not m.path.is_file():
            raise StageError(f"mod {m.id}: arquivo {m.path} não existe")
    out = ctx.workdir / "iso" / "patched.iso"
    key = key_of(ctx.game["iso_sha1"], *[sha1_file(m.path) for m in patches])
    ctx.effective_iso = out
    if out.is_file() and ctx.is_cached("patch_iso", key):
        return "cache"
    if not ctx.env.xdelta3.is_file():
        raise StageError(f"xdelta3 não encontrado em {ctx.env.xdelta3}")
    out.parent.mkdir(parents=True, exist_ok=True)
    source = ctx.iso
    for i, m in enumerate(patches):
        target = out if i == len(patches) - 1 else out.with_name(f"step{i}.iso")
        ctx.reporter.log(f"Aplicando {m.name}...")
        run_tool(ctx, "patch_iso", [str(ctx.env.xdelta3), "-d", "-f", "-s", str(source), str(m.path), str(target)])
        if source != ctx.iso:
            source.unlink()
        source = target
    ctx.mark("patch_iso", key)
    return f"{len(patches)} patch(es) aplicados -> {out.name}"


def extract(ctx: Context) -> str:
    iso_path = ctx.effective_iso or ctx.iso
    st = iso_path.stat()
    key = key_of(iso_path, st.st_size, st.st_mtime_ns)
    disc = ctx.workdir / "disc"
    if ctx.elf.is_file() and (disc / "index.json").is_file() and ctx.is_cached("extract", key):
        return "cache"
    with IsoReader(iso_path) as iso:
        files = iso.files()
        total = max(sum(e.size for e in files), 1)
        done = 0

        def on_bytes(n: int) -> None:
            nonlocal done
            ctx.check_cancelled()
            done += n
            ctx.reporter.progress("extract", done / total)

        for e in files:
            ctx.reporter.log(f"  {e.path} ({e.size:,} bytes, LBA {e.lba})")
            iso.extract(e, disc / e.path, on_bytes)
        index = [{"path": e.path, "lba": e.lba, "size": e.size} for e in files]
    (disc / "index.json").write_text(json.dumps(index, indent=2), "utf-8")
    ctx.elf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(disc / ctx.game["elf"], ctx.elf)
    ctx.mark("extract", key)
    return f"{len(files)} arquivos, {total / 1e6:.0f} MB"


def _run_ghidra(ctx: Context, stage: str, elf: Path, project: Path, toml: Path, csv: Path) -> None:
    """Uso de desenvolvimento: analisa o ELF no Ghidra (linguagem R5900) e exporta o mapa de funções."""
    script = ctx.env.ghidra_script_dir / "ExportPS2Functions.java"
    if not ctx.env.ghidra_headless.is_file():
        raise StageError(f"Ghidra headless não encontrado em {ctx.env.ghidra_headless}")
    project.mkdir(parents=True, exist_ok=True)
    toml.parent.mkdir(parents=True, exist_ok=True)
    ctx.reporter.log("Análise do Ghidra: leva vários minutos.")
    run_tool(ctx, stage, [
        str(ctx.env.ghidra_headless), str(project), "game", "-import", str(elf), "-overwrite",
        "-processor", "r5900:LE:32:default",
        "-scriptPath", str(ctx.env.ghidra_script_dir),
        "-postScript", script.name, str(toml), str(csv),
    ])
    if not (toml.is_file() and csv.is_file()):
        raise StageError(f"o Ghidra terminou sem gerar o mapa (ver work/logs/{stage}.log)")


_PATH_KEYS = ("input", "output", "ghidra_output")


def _set_config_paths(text: str, values: dict[str, str]) -> str:
    """Troca os caminhos de um TOML do ps2_recomp (chaves input/output/ghidra_output)."""
    for key in _PATH_KEYS:
        text, n = re.subn(rf"(?m)^{key}\s*=.*$", lambda m, k=key: f"{k} = {json.dumps(values[k])}", text, count=1)
        if n != 1:
            raise StageError(f"TOML do recompilador sem a chave {key}")
    return text


def bundled_map(ctx: Context) -> Path | None:
    """Pasta do mapa de funções incluído no app para este executável, se houver."""
    d = ctx.env.games / ctx.game["serial"]
    meta = d / "game.toml"
    if not (meta.is_file() and (d / "functions.csv").is_file() and (d / "recomp.toml").is_file()):
        return None
    with open(meta, "rb") as f:
        return d if tomllib.load(f).get("elf_sha1") == ctx.game["elf_sha1"] else None


def _count_rows(csv: Path) -> int:
    with open(csv, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f) - 1


def analyze(ctx: Context) -> str:
    """Instala o mapa de funções do executável em gen/.

    O mapa vem pronto dentro do app (games/<serial>/), gerado uma vez no desenvolvimento com
    `recomp export-map`. Vale também para o ELF com patches, que trocam bytes sem mudar o layout.
    O Ghidra só é chamado aqui como recurso de desenvolvimento, quando não há mapa incluído.
    """
    toml, csv = ctx.gen / "config.toml", ctx.gen / "functions.csv"
    paths = {"input": ctx.elf.as_posix(), "output": (ctx.gen / "output").as_posix(), "ghidra_output": csv.as_posix()}
    d = bundled_map(ctx)
    if d is not None:
        key = key_of("bundled", sha1_file(d / "functions.csv"), sha1_file(d / "recomp.toml"), *paths.values())
        if toml.is_file() and csv.is_file() and ctx.is_cached("analyze", key):
            return "cache"
        ctx.gen.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(d / "functions.csv", csv)
        toml.write_text(_set_config_paths((d / "recomp.toml").read_text("utf-8"), paths), "utf-8")
        ctx.mark("analyze", key)
        return f"mapa incluído no app: {_count_rows(csv)} funções/entradas"

    if not ctx.env.ghidra_headless.is_file():
        raise StageError(
            f"o app não tem mapa de funções para este executável ({ctx.game['serial']}, "
            f"SHA-1 {ctx.game['elf_sha1'][:12]}). O mapa é gerado no desenvolvimento com 'recomp export-map'.")
    script = ctx.env.ghidra_script_dir / "ExportPS2Functions.java"
    key = key_of("ghidra", sha1_file(ctx.elf), sha1_file(script), ctx.env.ghidra.name)
    if toml.is_file() and csv.is_file() and ctx.is_cached("analyze", key):
        return "cache"
    _run_ghidra(ctx, "analyze", ctx.elf, ctx.workdir / "ghidra", toml, csv)
    toml.write_text(_set_config_paths(toml.read_text("utf-8"), paths), "utf-8")
    ctx.mark("analyze", key)
    return f"sem mapa incluído; analisado no Ghidra (desenvolvimento): {_count_rows(csv)} funções/entradas"


def export_map(ctx: Context) -> str:
    """Desenvolvimento: gera com o Ghidra o mapa de funções do ELF ORIGINAL da ISO e grava em games/<serial>/."""
    ingest(ctx)
    mdir = ctx.workdir / "map"
    mdir.mkdir(parents=True, exist_ok=True)
    elf = mdir / ctx.game["elf"]
    with IsoReader(ctx.iso) as iso:
        elf.write_bytes(iso.read(ctx.game["elf"]))
    toml, csv = mdir / "config.toml", mdir / "functions.csv"
    _run_ghidra(ctx, "export-map", elf, mdir / "ghidra", toml, csv)
    d = ctx.env.games / ctx.game["serial"]
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(csv, d / "functions.csv")
    template = _set_config_paths(toml.read_text("utf-8"), {k: f"@{k.upper()}@" for k in _PATH_KEYS})
    (d / "recomp.toml").write_text(template, "utf-8")
    script = ctx.env.ghidra_script_dir / "ExportPS2Functions.java"
    g = ctx.game
    meta = [
        '# Gerado por "recomp export-map". Identifica o executável ao qual o mapa de funções pertence.',
        f'serial = "{g["serial"]}"',
        f'elf = "{g["elf"]}"',
        f'elf_sha1 = "{g["elf_sha1"]}"',
        f'elf_crc_pcsx2 = "{g["elf_crc_pcsx2"]}"',
        f'entry = "{g["entry"]}"',
        f"entries = {_count_rows(csv)}",
        f'ghidra = "{ctx.env.ghidra.name}"',
        f'export_script_sha1 = "{sha1_file(script)}"',
    ]
    (d / "game.toml").write_text("\n".join(meta) + "\n", "utf-8")
    return f"mapa de {g['serial']} gravado em {d} ({_count_rows(csv)} funções/entradas)"


def recompile(ctx: Context) -> str:
    toml, csv = ctx.gen / "config.toml", ctx.gen / "functions.csv"
    exe = ctx.env.ps2_recomp_exe
    if not exe.is_file():
        raise StageError(f"ps2_recomp não compilado: {exe}")
    out = ctx.gen / "output"
    key = key_of(sha1_file(toml), sha1_file(csv), sha1_file(ctx.elf), sha1_file(exe))
    if out.is_dir() and any(out.iterdir()) and ctx.is_cached("recompile", key):
        return "cache"
    if out.is_dir():
        shutil.rmtree(out)
    run_tool(ctx, "recompile", [str(exe), str(toml)], cwd=ctx.gen)
    cpp = list(out.glob("*.cpp"))
    if not cpp:
        raise StageError("ps2_recomp não gerou arquivos .cpp (ver work/logs/recompile.log)")
    ctx.mark("recompile", key)
    return f"{len(cpp)} arquivos .cpp, {sum(f.stat().st_size for f in cpp) / 1e6:.0f} MB de C++"


def build(ctx: Context) -> str:
    """Compila ps2EntryRunner com o C++ gerado lido direto de work/ (nada é copiado para o repositório)."""
    out = ctx.gen / "output"
    if not any(out.glob("*.cpp")):
        raise StageError("nada para compilar: rode a etapa de conversão antes")
    extra = ctx.env.games / ctx.game["serial"] / "src"  # fontes escritos à mão para o jogo (overrides)

    bdir = ctx.workdir / "build"
    if not (bdir / "build.ninja").is_file() or "build" in ctx.force:
        opt = f"{ctx.env.opt_level} -DNDEBUG"
        run_tool(ctx, "build-configure", [
            "cmake", "-S", str(ctx.env.ps2recomp_src), "-B", str(bdir), "-G", "Ninja",
            # RelWithDebInfo sem -g: o modo Release do PS2Recomp liga LTO, inviável com este volume de código.
            "-DCMAKE_BUILD_TYPE=RelWithDebInfo", f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={opt}",
            f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={opt}",
            f"-DPS2X_RUNNER_SOURCE_DIR={out.as_posix()}",
            f"-DPS2X_RUNNER_EXTRA_SOURCE_DIR={extra.as_posix() if extra.is_dir() else ''}",
            f"-DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE={ctx.env.unity_batch}",
            "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_CXX_COMPILER=g++",
            f"-DCMAKE_C_FLAGS={ctx.env.cxx_flags}", f"-DCMAKE_CXX_FLAGS={ctx.env.cxx_flags}",
            "-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            "-DPS2X_BUILD_STUDIO=OFF", "-DPS2X_BUILD_TEST=OFF", "-DPS2X_BUILD_ANALYZER=OFF",
            "-DPS2X_BUILD_RECOMP=OFF", "-DPS2X_ENABLE_FFMPEG=OFF",
        ])
    run_tool(ctx, "build", ["cmake", "--build", str(bdir), "--target", "ps2EntryRunner", "-j", str(ctx.env.jobs)])
    exe = next(bdir.rglob("ps2EntryRunner.exe"), None)
    if exe is None:
        raise StageError("o build terminou sem gerar ps2EntryRunner.exe")
    dest = ctx.env.out / ctx.game["serial"]
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(exe, dest / "game.exe")
    return f"{dest / 'game.exe'} ({exe.stat().st_size / 1e6:.0f} MB)"


def run_game(ctx: Context, seconds: float | None = None, max_log_bytes: int = 200 << 20) -> int | None:
    """Executa o jogo compilado apontando para o disco extraído.

    A saída vai para a interface e para work/logs/run.log (limitado, pois o runtime é muito verboso).
    Com `seconds`, encerra o processo após esse tempo e devolve None; senão devolve o código de saída.
    """
    if not ctx.game:
        ingest(ctx)
    exe = ctx.env.out / ctx.game["serial"] / "game.exe"
    elf = ctx.workdir / "disc" / ctx.game["elf"]
    if not exe.is_file():
        raise StageError(f"executável ainda não compilado: {exe}")
    if not elf.is_file():
        raise StageError(f"disco não extraído: {elf}")
    logs = ctx.env.work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ctx.reporter.log(f'> "{exe}" "{elf}"')
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.Popen([str(exe), str(elf)], cwd=exe.parent, env=tool_env(ctx.env), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    ctx.current_proc = p
    timed_out = False

    def on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        ctx.cancel()

    timer = threading.Timer(seconds, on_timeout) if seconds else None
    if timer:
        timer.start()
    written = 0
    try:
        with open(logs / "run.log", "w", encoding="utf-8", errors="replace") as logf:
            assert p.stdout is not None
            for line in p.stdout:
                if written < max_log_bytes:
                    logf.write(line)
                    written += len(line)
                    ctx.reporter.log(line.rstrip())
        code = p.wait()
    finally:
        if timer:
            timer.cancel()
        ctx.current_proc = None
    if timed_out:
        ctx.cancelled = False
        ctx.reporter.log(f"Encerrado após {seconds:.0f} s (limite pedido).")
        return None
    return code


STAGES: list[tuple[str, str, Callable[[Context], str]]] = [
    ("ingest", "Identificar ISO", ingest),
    ("patch_iso", "Aplicar patches xdelta", patch_iso),
    ("extract", "Extrair disco", extract),
    ("analyze", "Mapa de funções do executável", analyze),
    ("recompile", "Converter para C++", recompile),
    ("build", "Compilar executável", build),
]
STAGE_KEYS = [k for k, _, _ in STAGES]


def run_pipeline(ctx: Context, until: str = "build") -> bool:
    """Roda as etapas em ordem até `until`. Etapas com entrada inalterada são puladas pelo cache."""
    if until not in STAGE_KEYS:
        raise ValueError(f"etapa desconhecida: {until}")
    for key, _title, fn in STAGES:
        ctx.reporter.stage(key, "running")
        try:
            ctx.check_cancelled()
            detail = fn(ctx)
        except StageError as e:
            ctx.reporter.stage(key, "error", str(e))
            return False
        except Exception as e:  # erro inesperado: mostra o tipo para facilitar o diagnóstico
            ctx.reporter.stage(key, "error", f"{type(e).__name__}: {e}")
            return False
        ctx.reporter.stage(key, "cached" if detail == "cache" else "ok", "" if detail == "cache" else detail)
        if key == until:
            break
    return True
