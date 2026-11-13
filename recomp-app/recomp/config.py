"""Carrega env.toml e resolve os caminhos do projeto."""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

if getattr(sys, "frozen", False):
    # App empacotado: o executável fica na raiz do pacote, ao lado de recomp-app/, third_party/, patches/...
    ROOT = Path(sys.executable).resolve().parent
    APP_DIR = ROOT / "recomp-app"
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    ROOT = APP_DIR.parent


UNSET = ROOT / ".nao-configurado"  # caminho que nunca existe: ferramenta opcional sem caminho definido


def _abs(value: str) -> Path:
    if not value:
        return UNSET
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def _tool(value: str, fallback: str) -> str:
    """Ferramenta que vem no pacote, se existir; senão a do PATH."""
    p = _abs(value) if value else UNSET
    return str(p) if p.is_file() else fallback


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass
class Env:
    root: Path
    iso: Path
    work: Path
    patches: Path
    mods: Path
    textures: Path
    out: Path
    art: Path  # imagens do launcher fornecidas pelo usuário (logo.png, banner.png); nunca versionadas
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
    opt_level: str
    bucket_shift: int  # log2 da faixa de endereços agrupada em cada unidade de compilação
    games: Path  # metadados por jogo incluídos no app (mapa de funções, overrides)
    toolchain: str  # compilador do jogo: "gcc" (MinGW do PATH) ou "clang" (llvm-mingw)
    llvm_mingw: Path
    build_dir: Path  # raiz dos builds do jogo; UNSET = dentro de work/<serial>/
    build_system: str  # "sdk" (runtime pré-compilado + Ninja, como no pacote) ou "cmake" (desenvolvimento)
    sdk: Path  # SDK do runtime (bibliotecas, cabeçalhos, sdk.json)
    ninja: str  # ninja.exe do pacote, ou "ninja" do PATH

    @property
    def ps2_recomp_exe(self) -> Path:
        """Recompilador: o que vem no pacote do app (bin/) ou, no desenvolvimento, o compilado em work/."""
        bundled = APP_DIR / "bin" / "ps2_recomp.exe"
        return bundled if bundled.is_file() else self.ps2recomp_build / "ps2xRecomp" / "ps2_recomp.exe"

    @property
    def ps2_analyzer_exe(self) -> Path:
        return self.ps2recomp_build / "ps2xAnalyzer" / "ps2_analyzer.exe"

    @property
    def compilers(self) -> tuple[str, str]:
        """(cc, c++) do toolchain escolhido."""
        if self.toolchain == "clang":
            return str(self.llvm_mingw / "bin" / "clang.exe"), str(self.llvm_mingw / "bin" / "clang++.exe")
        return "gcc", "g++"

    @property
    def ghidra_headless(self) -> Path:
        return self.ghidra / "support" / "analyzeHeadless.bat"

    @property
    def ghidra_script_dir(self) -> Path:
        return self.ps2recomp_src / "ps2xRecomp" / "tools" / "ghidra"


def load_env(path: Path | None = None) -> Env:
    """Lê env.toml (versionado, padrões portáveis) e sobrepõe env.local.toml (caminhos desta máquina)."""
    path = path or APP_DIR / "env.toml"
    with open(path, "rb") as f:
        t = tomllib.load(f)
    # env.package.toml vem no pacote (clang + SDK do pacote); env.local.toml é desta máquina e vence tudo
    for layer in ("env.package.toml", "env.local.toml"):
        extra = path.with_name(layer)
        if extra.is_file():
            with open(extra, "rb") as f:
                t = _merge(t, tomllib.load(f))
    d, tools, b = t.get("dirs", {}), t.get("tools", {}), t.get("build", {})
    return Env(
        root=ROOT,
        iso=_abs(t.get("iso", "game.iso")),
        work=_abs(d.get("work", "work")),
        patches=_abs(d.get("patches", "patches")),
        mods=_abs(d.get("mods", "mods")),
        textures=_abs(d.get("textures", "textures")),
        art=_abs(d.get("art", "art")),
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
        opt_level=str(b.get("opt_level", "-O1")),
        bucket_shift=int(b.get("bucket_shift", 11)),
        games=APP_DIR / "games",
        toolchain=str(b.get("toolchain", "gcc")),
        llvm_mingw=_abs(tools.get("llvm_mingw", "tools/llvm-mingw")),
        build_dir=_abs(b.get("build_dir", "")),
        build_system=str(b.get("build_system", "cmake")),
        sdk=_abs(tools.get("sdk", "sdk")),
        ninja=_tool(tools.get("ninja", "tools/ninja/ninja.exe"), "ninja"),
    )
