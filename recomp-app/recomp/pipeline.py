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
from .mods import load_patches


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
    patches = [p for p in load_patches(ctx.env.patches) if p.enabled and p.type == "xdelta"]
    if not patches:
        ctx.effective_iso = ctx.iso
        return "nenhum patch xdelta ativo; usando a ISO original"
    for m in patches:
        if not m.path.is_file():
            raise StageError(f"patch {m.id}: arquivo {m.path} não existe")
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


def _stub_address(selector: str) -> int:
    return int(selector.rsplit("@", 1)[1], 16)


def _merge_sdk_stubs(template: str, sdk_toml: Path, manual_toml: Path | None = None) -> tuple[str, int, int, int]:
    """Junta ao TOML do Ghidra as funções do SDK que o ps2_analyzer reconhece por assinatura.

    O script do Ghidra só encontra os wrappers de chamada de sistema. Sem o resto do SDK ligado às
    implementações nativas do runtime, o jogo fica rodando o código original do SDK e trava, por
    exemplo, esperando o IOP responder em sceSifInitRpc.
    """
    with open(sdk_toml, "rb") as f:
        sdk = [x for x in tomllib.load(f)["general"].get("stubs", []) if "@" in x]
    base = [x for x in tomllib.loads(template)["general"].get("stubs", []) if "@" in x]
    taken = {_stub_address(x) for x in base}
    added = [x for x in sdk if _stub_address(x) not in taken]  # no mesmo endereço, vale o do Ghidra
    # Ligações feitas à mão (engenharia reversa) têm a última palavra sobre as automáticas.
    manual: list[str] = []
    if manual_toml is not None and manual_toml.is_file():
        with open(manual_toml, "rb") as f:
            manual = [x for x in tomllib.load(f).get("stubs", []) if "@" in x]
    overridden = {_stub_address(x) for x in manual}
    merged = sorted([x for x in base + added if _stub_address(x) not in overridden] + manual, key=_stub_address)
    block = "stubs = [\n" + "".join(f"  {json.dumps(x)},\n" for x in merged) + "]"
    text, n = re.subn(r"(?ms)^stubs\s*=\s*\[.*?^\]", lambda m: block, template, count=1)
    if n != 1:
        raise StageError("TOML do recompilador sem a lista stubs")
    return text, len(base), len(added), len(manual)


JR_RA = 0x03E00008
DATA_ENTRY_MIN_SIZE = 1024


def elf_code_section(elf: bytes) -> tuple[int, int]:
    """Faixa [início, fim) da seção executável que contém o ponto de entrada do ELF (a .text)."""
    entry, = struct.unpack_from("<I", elf, 24)
    shoff, = struct.unpack_from("<I", elf, 32)
    shnum, = struct.unpack_from("<H", elf, 48)
    for i in range(shnum):
        _name, _type, flags, addr, _off, size = struct.unpack_from("<IIIIII", elf, shoff + i * 40)
        if flags & 4 and addr <= entry < addr + size:
            return addr, addr + size
    raise StageError("ELF sem seção executável contendo o ponto de entrada")


def _elf_word_reader(elf: bytes) -> Callable[[int], int | None]:
    """Devolve uma função que lê a palavra de 32 bits de um endereço virtual (None se não estiver no arquivo)."""
    phoff, = struct.unpack_from("<I", elf, 28)
    phnum, = struct.unpack_from("<H", elf, 44)
    segments = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from("<IIIII", elf, phoff + i * 32)
        if p_type == 1:
            segments.append((p_vaddr, p_vaddr + p_filesz, p_offset))

    def word(addr: int) -> int | None:
        for start, end, offset in segments:
            if start <= addr and addr + 4 <= end:
                return struct.unpack_from("<I", elf, offset + addr - start)[0]
        return None

    return word


def drop_data_entries(rows: list[list[str]], elf: bytes) -> tuple[list[list[str]], list[str]]:
    """Remove do mapa entradas sintéticas que são tabelas de dados, não código.

    O Ghidra marca como executável tudo o que o ELF declara assim, e o exportador cria entradas
    `entry_*` para rótulos referenciados. Fora da .text isso inclui tabelas de dados enormes, que o
    recompilador traduziria como se fossem instruções. Código de verdade fora da .text existe (rotinas
    que o SDK copia para o kernel), então o critério é o conteúdo: uma entrada `entry_*` fora da
    .text, com 1 KB ou mais e sem nenhum `jr $ra`, é dado.
    Cada linha é [nome, início, fim, tamanho]. Devolve (linhas mantidas, nomes removidos).
    """
    text_start, text_end = elf_code_section(elf)
    word = _elf_word_reader(elf)
    kept, dropped = [], []
    for row in rows:
        name, start, end = row[0], int(row[1], 16), int(row[2], 16)
        outside = not (text_start <= start < text_end)
        if outside and name.startswith("entry_") and end - start >= DATA_ENTRY_MIN_SIZE:
            if not any(word(a) == JR_RA for a in range(start, end, 4)):
                dropped.append(name)
                continue
        kept.append(row)
    return kept, dropped


def export_map(ctx: Context, force_ghidra: bool = False) -> str:
    """Desenvolvimento: gera o mapa de funções do ELF ORIGINAL da ISO e grava em games/<serial>/.

    Duas fontes: o Ghidra (limites de função e wrappers de chamada de sistema) e o ps2_analyzer
    (funções do SDK da Sony reconhecidas por assinatura). O Ghidra só roda de novo com force_ghidra.
    """
    ingest(ctx)
    mdir = ctx.workdir / "map"
    mdir.mkdir(parents=True, exist_ok=True)
    elf = mdir / ctx.game["elf"]
    with IsoReader(ctx.iso) as iso:
        elf.write_bytes(iso.read(ctx.game["elf"]))
    toml, csv = mdir / "config.toml", mdir / "functions.csv"
    if force_ghidra or not (toml.is_file() and csv.is_file()):
        _run_ghidra(ctx, "export-map", elf, mdir / "ghidra", toml, csv)
    else:
        ctx.reporter.log("Reaproveitando a análise do Ghidra já feita (use --force para refazer).")
    analyzer = ctx.env.ps2_analyzer_exe
    if not analyzer.is_file():
        raise StageError(f"ps2_analyzer não compilado: {analyzer}")
    sdk_toml = mdir / "sdk.toml"
    run_tool(ctx, "export-map-sdk", [str(analyzer), str(elf), str(sdk_toml)], cwd=mdir)

    d = ctx.env.games / ctx.game["serial"]
    d.mkdir(parents=True, exist_ok=True)
    lines = csv.read_text("utf-8").splitlines()
    rows, dropped = drop_data_entries([line.split(",") for line in lines[1:] if line], elf.read_bytes())
    if dropped:
        ctx.reporter.log(f"Descartadas {len(dropped)} entradas que são dados, não código: {', '.join(dropped[:6])}")
    (d / "functions.csv").write_text("\n".join([lines[0], *(",".join(r) for r in rows)]) + "\n", "utf-8")
    template = _set_config_paths(toml.read_text("utf-8"), {k: f"@{k.upper()}@" for k in _PATH_KEYS})
    template, n_ghidra, n_sdk, _ = _merge_sdk_stubs(template, sdk_toml)
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
        f"entries = {len(rows)}",
        f"dropped_data_entries = {len(dropped)}",
        f"stubs_syscall = {n_ghidra}",
        f"stubs_sdk = {n_sdk}",
        f'ghidra = "{ctx.env.ghidra.name}"',
        f'export_script_sha1 = "{sha1_file(script)}"',
    ]
    (d / "game.toml").write_text("\n".join(meta) + "\n", "utf-8")
    return (f"mapa de {g['serial']} gravado em {d}: {len(rows)} funções/entradas, "
            f"{n_ghidra} chamadas de sistema + {n_sdk} funções do SDK ligadas ao runtime")


def _same_file(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(1 << 20), fb.read(1 << 20)
            if ca != cb:
                return False
            if not ca:
                return True


def sync_generated(new: Path, out: Path) -> tuple[int, int]:
    """Leva para `out` só o que mudou em `new` e apaga o que sumiu. Devolve (alterados, removidos).

    Arquivo igual não é tocado, então mantém a data e o compilador não o refaz.
    """
    out.mkdir(parents=True, exist_ok=True)
    fresh = {e.name for e in os.scandir(new) if e.is_file()}
    changed = 0
    for name in fresh:
        src, dst = new / name, out / name
        if dst.is_file() and _same_file(src, dst):
            continue
        os.replace(src, dst)
        changed += 1
    removed = 0
    for e in list(os.scandir(out)):
        if e.is_file() and e.name not in fresh:
            os.unlink(e.path)
            removed += 1
    return changed, removed


def recompile(ctx: Context) -> str:
    toml, csv = ctx.gen / "config.toml", ctx.gen / "functions.csv"
    exe = ctx.env.ps2_recomp_exe
    if not exe.is_file():
        raise StageError(f"ps2_recomp não compilado: {exe}")
    out, new = ctx.gen / "output", ctx.gen / "output.new"
    key = key_of(sha1_file(toml), sha1_file(csv), sha1_file(ctx.elf), sha1_file(exe))
    if out.is_dir() and any(out.iterdir()) and ctx.is_cached("recompile", key):
        return "cache"
    # Gera numa pasta à parte e sincroniza: quem não mudou continua com a mesma data.
    shutil.rmtree(new, ignore_errors=True)
    run_toml = ctx.gen / "config.run.toml"
    paths = {"input": ctx.elf.as_posix(), "output": new.as_posix(), "ghidra_output": csv.as_posix()}
    run_toml.write_text(_set_config_paths(toml.read_text("utf-8"), paths), "utf-8")
    run_tool(ctx, "recompile", [str(exe), str(run_toml)], cwd=ctx.gen)
    if not any(new.glob("*.cpp")):
        raise StageError("ps2_recomp não gerou arquivos .cpp (ver work/logs/recompile.log)")
    ctx.reporter.log("Comparando com a geração anterior...")
    changed, removed = sync_generated(new, out)
    shutil.rmtree(new, ignore_errors=True)
    cpp = list(out.glob("*.cpp"))
    ctx.mark("recompile", key)
    return (f"{len(cpp)} arquivos .cpp, {sum(f.stat().st_size for f in cpp) / 1e6:.0f} MB de C++ "
            f"({changed} alterados, {removed} removidos)")


_ADDRESS_SUFFIX = re.compile(r"_0x([0-9a-fA-F]+)\.cpp$")


def write_unity_buckets(out: Path, unity: Path, shift: int) -> tuple[int, int]:
    """Agrupa os .cpp gerados em unidades de compilação por faixa de endereço.

    Cada unidade inclui as funções cujo endereço cai na mesma faixa de 2^shift bytes. O grupo de uma
    função depende só do endereço dela, não do nome nem das vizinhas, então mudar ou renomear uma
    função recompila uma unidade só. Devolve (unidades, unidades reescritas).
    """
    unity.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[str]] = {}
    for name in sorted(e.name for e in os.scandir(out) if e.is_file() and e.name.endswith(".cpp")):
        m = _ADDRESS_SUFFIX.search(name)
        bucket = f"u_{int(m.group(1), 16) >> shift:06x}" if m else "x_" + name[:-4]
        buckets.setdefault(bucket, []).append(name)
    wanted: dict[str, str] = {
        f"{bucket}.cpp": "".join(f'#include "{(out / n).as_posix()}"\n' for n in names)
        for bucket, names in buckets.items()
    }
    rewritten = 0
    for fname, content in wanted.items():
        path = unity / fname
        if not path.is_file() or path.read_text("utf-8") != content:
            path.write_text(content, "utf-8")
            rewritten += 1
    for header in out.glob("*.h"):  # o CMake procura os cabeçalhos gerados ao lado das unidades
        dst = unity / header.name
        if not dst.is_file() or not _same_file(header, dst):
            shutil.copyfile(header, dst)
    for e in list(os.scandir(unity)):
        if e.is_file() and e.name.endswith(".cpp") and e.name not in wanted:
            os.unlink(e.path)
    return len(wanted), rewritten


def runtime_handlers(call_list: Path) -> dict[str, str]:
    """Nome de cada handler do runtime -> namespace C++ (ps2_syscalls ou ps2_stubs), lido de ps2_call_list.h."""
    handlers: dict[str, str] = {}
    namespace = None
    for line in call_list.read_text("utf-8", errors="replace").splitlines():
        if line.startswith("#define PS2_SYSCALL_LIST"):
            namespace = "ps2_syscalls"
        elif line.startswith("#define PS2_STUB_LIST"):
            namespace = "ps2_stubs"
        elif line.startswith("#define"):
            namespace = None
        elif namespace:
            for name in re.findall(r"\bX\((\w+)\)", line):
                handlers.setdefault(name, namespace)
    return handlers


def load_manual_stubs(path: Path) -> list[tuple[str, int]]:
    """Lê games/<serial>/stubs.toml: lista de "handler@0xENDERECO" identificados por engenharia reversa."""
    if not path.is_file():
        return []
    with open(path, "rb") as f:
        entries = tomllib.load(f).get("stubs", [])
    out = []
    for entry in entries:
        if "@" not in entry:
            raise StageError(f"{path.name}: '{entry}' precisa ter a forma handler@0xENDERECO")
        name, address = entry.rsplit("@", 1)
        out.append((name, int(address, 16)))
    return out


def render_manual_stubs(stubs: list[tuple[str, int]], handlers: dict[str, str], elf_name: str, entry: int) -> str:
    """Fonte C++ que liga em runtime cada endereço ao handler nativo, do mesmo jeito que o recompilador faria.

    Aplicar em runtime (game override) em vez de pelo TOML evita regenerar o cabeçalho de stubs, que
    todas as unidades incluem: acrescentar uma ligação recompila só este arquivo.
    """
    lines = [
        "// Gerado pelo recomp-app a partir de games/<serial>/stubs.toml. Não editar à mão.",
        '#include "game_overrides.h"',
        '#include "ps2_runtime.h"',
        '#include "ps2_stubs.h"',
        '#include "ps2_syscalls.h"',
        "#include <iostream>",
        "",
        "namespace",
        "{",
    ]
    for name, address in stubs:
        if name not in handlers:
            raise StageError(f"stubs.toml: o runtime não tem um handler chamado '{name}' (endereço 0x{address:08X})")
        lines += [
            f"    void manual_{name}_0x{address:08x}(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)",
            "    {",
            "        ctx->pc = getRegU32(ctx, 31); // volta para quem chamou, como o wrapper do recompilador",
            f"        {handlers[name]}::{name}(rdram, ctx, runtime);",
            "    }",
            "",
        ]
    lines += ["    void applyManualStubs(PS2Runtime &runtime)", "    {"]
    for name, address in stubs:
        lines += [
            f"        if (!runtime.replaceFunction(0x{address:08X}u, &manual_{name}_0x{address:08x}))",
            f'            std::cerr << "[recomp-app] falha ao ligar {name} em 0x{address:08X}" << std::endl;',
        ]
    lines += [
        f'        std::cout << "[recomp-app] {len(stubs)} ligacoes manuais aplicadas" << std::endl;',
        "    }",
        "}",
        "",
        f'PS2_REGISTER_GAME_OVERRIDE("recomp-app manual stubs", "{elf_name}", 0x{entry:08X}u, 0u, applyManualStubs)',
        "",
    ]
    return "\n".join(lines)


def write_overrides(ctx: Context, dest: Path) -> int:
    """Monta a pasta de fontes extras do jogo: ligações manuais geradas + fontes escritos à mão em games/<serial>/src."""
    gdir = ctx.env.games / ctx.game["serial"]
    stubs = load_manual_stubs(gdir / "stubs.toml")
    handlers = runtime_handlers(ctx.env.ps2recomp_src / "ps2xRuntime" / "include" / "ps2_call_list.h")
    wanted = {"recomp_manual_stubs.cpp": render_manual_stubs(stubs, handlers, ctx.game["elf"], int(ctx.game["entry"], 16))}
    if (gdir / "src").is_dir():
        for f in sorted((gdir / "src").iterdir()):
            if f.suffix in (".cpp", ".h", ".hpp"):
                wanted[f.name] = f.read_text("utf-8")
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in wanted.items():
        path = dest / name
        if not path.is_file() or path.read_text("utf-8") != content:
            path.write_text(content, "utf-8")
    for e in list(os.scandir(dest)):
        if e.is_file() and e.name not in wanted:
            os.unlink(e.path)
    return len(stubs)


def build(ctx: Context) -> str:
    """Compila ps2EntryRunner com o C++ gerado lido direto de work/ (nada é copiado para o repositório)."""
    out = ctx.gen / "output"
    if not any(out.glob("*.cpp")):
        raise StageError("nada para compilar: rode a etapa de conversão antes")
    unity = ctx.gen / "unity"
    units, rewritten = write_unity_buckets(out, unity, ctx.env.bucket_shift)
    ctx.reporter.log(f"{units} unidades de compilação ({rewritten} novas ou alteradas na lista de arquivos).")
    extra = ctx.gen / "overrides"
    n_manual = write_overrides(ctx, extra)
    ctx.reporter.log(f"{n_manual} ligações manuais do jogo aplicadas em runtime.")

    bdir = ctx.workdir / "build"
    opt = f"{ctx.env.opt_level} -DNDEBUG"
    configure = [
        "cmake", "-S", str(ctx.env.ps2recomp_src), "-B", str(bdir), "-G", "Ninja",
        # RelWithDebInfo sem -g: o modo Release do PS2Recomp liga LTO, inviável com este volume de código.
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo", f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={opt}",
        f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={opt}",
        f"-DPS2X_RUNNER_SOURCE_DIR={unity.as_posix()}",
        f"-DPS2X_RUNNER_EXTRA_SOURCE_DIR={extra.as_posix()}",
        "-DPS2X_ENABLE_RUNNER_UNITY_BUILD=OFF",  # o agrupamento é o nosso, por faixa de endereço
        "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_CXX_COMPILER=g++",
        f"-DCMAKE_C_FLAGS={ctx.env.cxx_flags}", f"-DCMAKE_CXX_FLAGS={ctx.env.cxx_flags}",
        "-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        "-DPS2X_BUILD_STUDIO=OFF", "-DPS2X_BUILD_TEST=OFF", "-DPS2X_BUILD_ANALYZER=OFF",
        "-DPS2X_BUILD_RECOMP=OFF", "-DPS2X_ENABLE_FFMPEG=OFF",
    ]
    cfg_key = key_of(*configure)
    if not (bdir / "build.ninja").is_file() or "build" in ctx.force or not ctx.is_cached("build_configure", cfg_key):
        run_tool(ctx, "build-configure", configure)
        ctx.mark("build_configure", cfg_key)
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
