"""Checagem do ambiente: o que o pipeline precisa e o que está faltando."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Env
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


def _path(name: str, p: Path, required: bool = True, hint: str = "") -> Check:
    ok = p.exists()
    return Check(name, ok, str(p) if ok else f"ausente: {p}{' (' + hint + ')' if hint else ''}", required)


def run_doctor(env: Env) -> list[Check]:
    ext = env.ghidra / "Ghidra" / "Extensions" / "ghidra-emotionengine-reloaded"
    free = shutil.disk_usage(env.root).free / 1e9
    return [
        _cmd(env, "git", ["--version"]),
        _cmd(env, "cmake", ["--version"]),
        _cmd(env, "ninja", ["--version"]),
        _cmd(env, "g++", ["--version"]),
        _cmd(env, "ccache", ["--version"], required=False),
        _path("ISO do jogo", env.iso),
        _path("Ghidra headless", env.ghidra_headless),
        _path("Ghidra: extensão R5900 (PS2)", ext, hint="ghidra-emotionengine-reloaded"),
        _path("JDK 21 (JAVA_HOME do pipeline)", env.java_home / "bin" / "java.exe"),
        _path("xdelta3", env.xdelta3),
        _path("PS2Recomp (código)", env.ps2recomp_src / "CMakeLists.txt"),
        _path("ps2_recomp.exe", env.ps2_recomp_exe, hint="compilar o PS2Recomp"),
        _path("ps2_analyzer.exe", env.ps2_analyzer_exe, required=False, hint="compilar o PS2Recomp"),
        _path("PCSX2 (emulador de referência)", env.pcsx2, required=False),
        _path("PCSX2 (código de referência)", env.pcsx2_src / "pcsx2", required=False),
        Check("Espaço livre no disco do projeto", free >= 15, f"{free:.1f} GB livres (mínimo sugerido: 15 GB)"),
    ]
