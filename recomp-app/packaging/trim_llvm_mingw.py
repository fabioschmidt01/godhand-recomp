"""Corta do llvm-mingw o que o pacote não usa: só fica o clang para Windows x86-64.

Uso: python trim_llvm_mingw.py <pasta do llvm-mingw>
"""
import shutil
import sys
from pathlib import Path

OTHER_TARGETS = ("aarch64", "armv7", "i686", "arm64ec")
DROP_DIRS = ["python", "share", "lib/libear", "lib/libscanbuild", "lib/clang/{ver}/lib/linux",
             *[f"{t}-w64-mingw32" for t in OTHER_TARGETS]]
DROP_BIN_PREFIXES = ("lldb", "liblldb", "clangd", "clang-tidy", "libpython", "libclang_rt.asan", "busybox",
                     *(f"{t}-w64-" for t in OTHER_TARGETS))


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main(root: Path) -> None:
    before = size(root)
    ver = next((root / "lib" / "clang").iterdir()).name
    for rel in DROP_DIRS:
        p = root / rel.format(ver=ver)
        if p.exists():
            shutil.rmtree(p)
    for f in (root / "bin").iterdir():
        if f.name.lower().startswith(DROP_BIN_PREFIXES):
            f.unlink() if f.is_file() else shutil.rmtree(f)
    print(f"llvm-mingw: {before / 1e6:.0f} MB -> {size(root) / 1e6:.0f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
