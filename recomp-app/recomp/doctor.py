"""Checagem do ambiente: o que o pipeline precisa e o que está faltando."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .bios import BiosError, identify_bios
from .config import Env
from .settings import bios_path, load_settings
from .pipeline import tool_env


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _version(env: Env, cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=tool_env(env),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return out[0] if out else ""


def _cmd(env: Env, name: str, args: list[str], required: bool = True) -> Check:
    v = _version(env, [name, *args])
    return Check(name, v is not None, v if v is not None else "não encontrado no PATH", required)


def _path(name: str, p, required: bool = True, hint: str = "") -> Check:
    p = Path(p)
    ok = p.exists()
    return Check(name, ok, str(p) if ok else f"ausente: {p}{' (' + hint + ')' if hint else ''}", required)


def _bios_check(env: Env) -> Check:
    path = bios_path(env)
    if path is None:
        return Check("BIOS do PS2", False, "não escolhida (necessária para o IOP nas próximas versões)", required=False)
    try:
        return Check("BIOS do PS2", True, f"{identify_bios(path).describe()}: {path}", required=False)
    except BiosError as e:
        return Check("BIOS do PS2", False, str(e), required=False)


def run_doctor(env: Env) -> list[Check]:
    ext = env.ghidra / "Ghidra" / "Extensions" / "ghidra-emotionengine-reloaded"
    free = shutil.disk_usage(env.root).free / 1e9
    maps = sorted(d.name for d in env.games.glob("*") if (d / "functions.csv").is_file()) if env.games.is_dir() else []
    package = env.build_system == "sdk"  # modo do pacote: runtime pré-compilado, clang e ninja inclusos
    checks = [_path("ISO do jogo", Path(load_settings(env).get("iso") or env.iso)), _bios_check(env)]
    if package:
        checks += [
            _path("Compilador (clang do pacote)", env.compilers[1], hint="tools/llvm-mingw"),
            Check("Ninja", Path(env.ninja).is_file() or shutil.which(env.ninja) is not None, env.ninja),
            _path("SDK do runtime", env.sdk / "sdk.json", required=not (env.ps2recomp_src / "CMakeLists.txt").is_file(),
                  hint="vem no pacote; no desenvolvimento é gerado na primeira preparação"),
        ]
    else:
        checks += [
            _cmd(env, "git", ["--version"]),
            _cmd(env, "cmake", ["--version"]),
            _cmd(env, "ninja", ["--version"]),
            _cmd(env, "g++", ["--version"], required=env.toolchain == "gcc"),
            _path("clang (llvm-mingw)", env.llvm_mingw / "bin" / "clang++.exe", required=env.toolchain == "clang",
                  hint="tools/llvm-mingw"),
            _cmd(env, "ccache", ["--version"], required=False),
            _path("PS2Recomp (código)", env.ps2recomp_src / "CMakeLists.txt"),
        ]
    checks += [
        Check("Mapas de funções incluídos no app", bool(maps), ", ".join(maps) if maps else f"nenhum em {env.games}"),
        _path("xdelta3", env.xdelta3),
        _path("ps2_recomp.exe", env.ps2_recomp_exe, hint="vem no pacote"),
    ]
    if not package or env.ghidra.exists():  # ferramentas de desenvolvimento: só aparecem para quem as tem
        checks += [
            _path("Ghidra headless (só desenvolvimento)", env.ghidra_headless, required=False),
            _path("Ghidra: extensão R5900 (só desenvolvimento)", ext, required=False, hint="ghidra-emotionengine-reloaded"),
            _path("JDK 21 (só desenvolvimento)", env.java_home / "bin" / "java.exe", required=False),
            _path("ps2_analyzer.exe (só desenvolvimento)", env.ps2_analyzer_exe, required=False),
            _path("PCSX2 (emulador de referência)", env.pcsx2, required=False),
            _path("PCSX2 (código de referência)", env.pcsx2_src / "pcsx2", required=False),
        ]
    checks.append(Check("Espaço livre no disco do jogo", free >= 15, f"{free:.1f} GB livres (mínimo sugerido: 15 GB)"))
    return checks
