"""SDK do runtime e build do jogo sem CMake.

O runtime do PS2Recomp (e o raylib, o imgui e o IOP) é compilado uma vez, com o mesmo clang que vai no pacote,
e guardado como "SDK": bibliotecas estáticas, cabeçalhos e um sdk.json com as flags. No PC de quem joga, o app só
compila o código gerado do jogo e liga tudo com o Ninja que vem no pacote: não precisa de CMake, Git nem internet.

Desenvolvimento: `recomp sdk` gera o SDK a partir de third_party/PS2Recomp (precisa de CMake e internet na primeira
vez, porque o CMake baixa o raylib e o imgui). O Actions faz o mesmo e põe o SDK no pacote.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import Env

HEADER_EXT = {".h", ".hpp", ".hxx", ".inl", ".inc"}
SDK_TARGETS = ["ps2_runtime", "ps2_iop", "raylib", "imgui", "rlImGui"]
RUNNER_SOURCES = ["src/main.cpp", "src/lib/ps2_debug_panel.cpp"]  # partes fixas do executável, compiladas no SDK
PCH_HEADERS = ["ps2_runtime_macros.h", "ps2_runtime.h", "ps2_syscalls.h", "ps2_stubs.h"]


class SdkError(RuntimeError):
    pass


def split_command(command: str) -> list[str]:
    """Divide uma linha de comando do Windows respeitando aspas no meio do argumento (-I"C:/a b/c")."""
    return [re.sub(r'"', "", tok) for tok in re.findall(r'(?:[^\s"]+|"[^"]*")+', command)]


def _git_rev(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except OSError:
        return ""


def _ninja_escape(path: str) -> str:
    return path.replace("$", "$$").replace(" ", "$ ").replace(":", "$:")


def _quote(path: str | Path) -> str:
    return f'"{Path(path).as_posix()}"'


def _copy_headers(src: Path, dst: Path) -> int:
    count = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in (".git", "CMakeFiles", "examples", "docs", "tests")]
        for name in files:
            if Path(name).suffix.lower() in HEADER_EXT:
                rel = Path(root, name).relative_to(src)
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(root, name), dst / rel)
                count += 1
    return count


def build_sdk(env: Env, run, log, out: Path, opt: str = "-O2") -> str:
    """Compila o runtime com o clang do pacote e monta o SDK em `out`.

    `run(stage, cmd)` executa um processo (com log); `log(msg)` escreve uma linha.
    """
    cc, cxx = env.compilers
    if env.toolchain != "clang":
        raise SdkError('o SDK é gerado com o clang que vai no pacote: use toolchain = "clang"')
    bdir = env.work / "build" / "sdk-clang"
    empty = bdir / "runner-vazio"  # o SDK não tem código de jogo: só as partes fixas do executável
    empty.mkdir(parents=True, exist_ok=True)
    run("sdk-configure", [
        "cmake", "-S", str(env.ps2recomp_src), "-B", str(bdir), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo", f"-DCMAKE_CXX_FLAGS_RELWITHDEBINFO={opt} -DNDEBUG",
        f"-DCMAKE_C_FLAGS_RELWITHDEBINFO={opt} -DNDEBUG",
        f"-DCMAKE_C_COMPILER={Path(cc).as_posix()}", f"-DCMAKE_CXX_COMPILER={Path(cxx).as_posix()}",
        f"-DCMAKE_C_FLAGS={env.cxx_flags}", f"-DCMAKE_CXX_FLAGS={env.cxx_flags}",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5", f"-DPS2X_RUNNER_SOURCE_DIR={empty.as_posix()}",
        "-DPS2X_ENABLE_RUNNER_UNITY_BUILD=OFF", "-DPS2X_ENABLE_RUNNER_PCH=OFF",
        "-DPS2X_BUILD_STUDIO=OFF", "-DPS2X_BUILD_TEST=OFF", "-DPS2X_BUILD_ANALYZER=OFF",
        "-DPS2X_BUILD_RECOMP=OFF", "-DPS2X_ENABLE_FFMPEG=OFF",
    ])
    commands = json.loads((bdir / "compile_commands.json").read_text("utf-8"))
    runtime_dir = (env.ps2recomp_src / "ps2xRuntime").resolve()
    runner = {}
    for c in commands:
        src = Path(c["file"]).resolve()
        for rel in RUNNER_SOURCES:
            if src == (runtime_dir / rel).resolve():
                runner[rel] = c
    if len(runner) != len(RUNNER_SOURCES):
        raise SdkError(f"não achei no CMake os fontes fixos do executável: {sorted(set(RUNNER_SOURCES) - set(runner))}")
    objects = [os.path.relpath(Path(bdir, c["output"]), bdir).replace("\\", "/") for c in runner.values()]
    run("sdk-build", ["cmake", "--build", str(bdir), "-j", str(env.jobs), "--target", *SDK_TARGETS])
    run("sdk-build-runner", ["ninja", "-C", str(bdir), *objects])

    # flags de quem compila código do executável (as mesmas para o código gerado do jogo)
    args = split_command(runner[RUNNER_SOURCES[0]]["command"])
    defines, includes, flags = [], [], []
    skip = False
    for a in args[1:]:
        if skip:
            skip = False
            continue
        if a in ("-o", "-c", "-include", "-MF", "-MT", "-MQ"):
            skip = True
            continue
        if a.startswith("-D"):
            defines.append(a)
        elif a.startswith("-I"):
            includes.append(a[2:])
        elif a.startswith("-") and a not in ("-MD", "-Winvalid-pch") and not a.startswith(("-O", "-W")):
            flags.append(a)
    ninja_file = (bdir / "build.ninja").read_text("utf-8", errors="replace")
    m = re.search(r"build ps2xRuntime/ps2EntryRunner\.exe:.*?\n(?:  .*\n)*?  LINK_LIBRARIES = (.*)\n", ninja_file)
    if not m:
        raise SdkError("não achei a linha de ligação do executável no build.ninja")
    link = m.group(1).split()
    libs = [x for x in link if not x.startswith("-")]
    syslibs = [x for x in link if x.startswith("-l")]

    if out.exists():
        shutil.rmtree(out)
    (out / "lib").mkdir(parents=True)
    sdk_includes, headers = [], 0
    for i, inc in enumerate(includes):
        p = Path(inc)
        if p.resolve() == empty.resolve():
            continue
        dst = out / "include" / f"i{i:02d}"
        headers += _copy_headers(p, dst)
        sdk_includes.append(dst.relative_to(out).as_posix())
    if headers == 0:
        raise SdkError("nenhum cabeçalho copiado para o SDK: os caminhos de include não foram encontrados")
    sdk_libs = []
    for lib in libs:
        src = bdir / lib
        shutil.copy2(src, out / "lib" / src.name)
        sdk_libs.append(f"lib/{src.name}")
    sdk_objs = []
    for obj in objects:
        name = Path(obj).name.replace(".cpp.obj", ".o")
        shutil.copy2(bdir / obj, out / "lib" / name)
        sdk_objs.append(f"lib/{name}")
    meta = {
        "toolchain": env.toolchain,
        "compiler": subprocess.run([cxx, "--version"], capture_output=True, text=True).stdout.splitlines()[0],
        "ps2recomp": _git_rev(env.ps2recomp_src),
        "opt": opt,
        "defines": defines,
        "includes": sdk_includes,
        "flags": flags,
        "objects": sdk_objs,
        "libs": sdk_libs,
        "syslibs": syslibs,
        "pch_headers": PCH_HEADERS,
    }
    (out / "sdk.json").write_text(json.dumps(meta, indent=2), "utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log(f"SDK: {len(sdk_libs)} bibliotecas, {headers} cabeçalhos, {size / 1e6:.0f} MB em {out}")
    return f"SDK gerado em {out} ({size / 1e6:.0f} MB)"


def load_sdk(sdk: Path) -> dict:
    meta_file = sdk / "sdk.json"
    if not meta_file.is_file():
        raise SdkError(f"SDK do runtime não encontrado em {sdk}")
    return json.loads(meta_file.read_text("utf-8"))


def write_game_ninja(env: Env, sdk: Path, unity: Path, extra: Path, build: Path, exe_name: str, opt: str) -> Path:
    """Gera o build.ninja que compila o código do jogo e liga com o SDK."""
    meta = load_sdk(sdk)
    cc, cxx = env.compilers
    build.mkdir(parents=True, exist_ok=True)
    pch = build / "pch.hpp"
    pch_lines = [f'#include "{h}"' for h in meta["pch_headers"]]
    pch_lines += [f'#include "{h}"' for h in ("ps2_recompiled_functions.h", "ps2_recompiled_stubs.h")
                  if (unity / h).is_file()]
    content = "\n".join(pch_lines) + "\n"
    if not pch.is_file() or pch.read_text("utf-8") != content:
        pch.write_text(content, "utf-8")

    includes = [_quote(unity)] + [_quote(sdk / i) for i in meta["includes"]]
    cxxflags = " ".join([*meta["defines"], *(f"-I{i}" for i in includes), *meta["flags"], opt, "-DNDEBUG",
                         "-Wno-everything"])  # código gerado: avisos não ajudam e custam tempo
    libs = " ".join([*(_quote(sdk / o) for o in meta["objects"]), *(_quote(sdk / l) for l in meta["libs"]),
                     *meta["syslibs"]])
    lines = [
        "# Gerado pelo recomp-app. Compila o código do jogo e liga com o SDK do runtime.",
        f"cxx = {_quote(cxx)}",
        f"cxxflags = {cxxflags}",
        f"libs = {libs}",
        "",
        "rule pch",
        "  command = $cxx $cxxflags -x c++-header $in -o $out -MD -MF $out.d",
        "  depfile = $out.d",
        "  deps = gcc",
        "  description = PCH $out",
        "rule cxx",
        "  command = $cxx $cxxflags -include-pch pch.hpp.pch -c $in -o $out -MD -MF $out.d",
        "  depfile = $out.d",
        "  deps = gcc",
        "  description = CXX $out",
        "rule cxx_plain",
        "  command = $cxx $cxxflags -c $in -o $out -MD -MF $out.d",
        "  depfile = $out.d",
        "  deps = gcc",
        "  description = CXX $out",
        "rule link",
        "  command = $cxx @$out.rsp -o $out",
        "  rspfile = $out.rsp",
        "  rspfile_content = $in $libs -static -Wl,--stack,8388608",
        "  description = LINK $out",
        "",
        "build pch.hpp.pch: pch pch.hpp",
    ]
    objects = []
    for src in sorted(unity.glob("*.cpp")):
        obj = f"obj/{src.stem}.o"
        lines.append(f"build {obj}: cxx {_ninja_escape(src.as_posix())} | pch.hpp.pch")
        objects.append(obj)
    for src in sorted(extra.glob("*.cpp")):  # fontes escritos à mão: sem PCH, para não depender do gerado
        obj = f"obj/extra_{src.stem}.o"
        lines.append(f"build {obj}: cxx_plain {_ninja_escape(src.as_posix())}")
        objects.append(obj)
    sdk_inputs = " ".join(_ninja_escape((sdk / x).as_posix()) for x in [*meta["objects"], *meta["libs"]])
    lines.append(f"build {exe_name}: link {' '.join(objects)} | {sdk_inputs}")
    lines.append(f"default {exe_name}")
    (build / "build.ninja").write_text("\n".join(lines) + "\n", "utf-8")
    return build / exe_name
