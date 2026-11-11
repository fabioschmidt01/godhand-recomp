"""Carrega env.toml e resolve os caminhos do projeto."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent


def _abs(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


@dataclass
class Env:
    root: Path
    iso: Path
    work: Path
    mods: Path
    out: Path
    ps2recomp_src: Path
    ps2recomp_build: Path
    ghidra: Path
    java_home: Path
    xdelta3: Path
    pcsx2: Path
    pcsx2_src: Path
    mingw_bin: Path
    jobs: int
    cxx_flags: str
    ccache_dir: Path

    @property
    def ps2_recomp_exe(self) -> Path:
        return self.ps2recomp_build / "ps2xRecomp" / "ps2_recomp.exe"

    @property
    def ps2_analyzer_exe(self) -> Path:
        return self.ps2recomp_build / "ps2xAnalyzer" / "ps2_analyzer.exe"

    @property
    def ghidra_headless(self) -> Path:
        return self.ghidra / "support" / "analyzeHeadless.bat"

    @property
    def ghidra_script_dir(self) -> Path:
        return self.ps2recomp_src / "ps2xRecomp" / "tools" / "ghidra"


def load_env(path: Path | None = None) -> Env:
    with open(path or APP_DIR / "env.toml", "rb") as f:
        t = tomllib.load(f)
    d, tools, b = t.get("dirs", {}), t.get("tools", {}), t.get("build", {})
    return Env(
        root=ROOT,
        iso=_abs(t.get("iso", "game.iso")),
        work=_abs(d.get("work", "work")),
        mods=_abs(d.get("mods", "mods")),
        out=_abs(d.get("out", "out")),
        ps2recomp_src=_abs(tools.get("ps2recomp_src", "third_party/PS2Recomp")),
        ps2recomp_build=_abs(tools.get("ps2recomp_build", "work/build/ps2recomp")),
        ghidra=_abs(tools.get("ghidra", "")),
        java_home=_abs(tools.get("java_home", "")),
        xdelta3=_abs(tools.get("xdelta3", "tools/xdelta3/xdelta3.exe")),
        pcsx2=_abs(tools.get("pcsx2", "")),
        pcsx2_src=_abs(tools.get("pcsx2_src", "refs/pcsx2")),
        mingw_bin=_abs(tools.get("mingw_bin", "")),
        jobs=int(b.get("jobs", 8)),
        cxx_flags=str(b.get("cxx_flags", "-msse4.1")),
        ccache_dir=_abs(b.get("ccache_dir", "work/ccache")),
    )
