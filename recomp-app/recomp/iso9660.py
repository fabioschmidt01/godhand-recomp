"""Leitor mínimo de ISO9660 (o suficiente para discos de PS2).

Guarda o LBA de cada arquivo: o jogo lê o disco por setor, então essa tabela
é necessária mais tarde para servir leituras de CD no executável nativo.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SECTOR = 2048


@dataclass
class IsoEntry:
    path: str  # com "/" e no caso original do disco
    lba: int
    size: int
    is_dir: bool


class IsoReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.f = open(self.path, "rb")
        self.f.seek(16 * SECTOR)
        pvd = self.f.read(SECTOR)
        if pvd[1:6] != b"CD001":
            self.f.close()
            raise ValueError(f"{self.path.name} não é uma imagem ISO9660")
        root = pvd[156:190]
        self.entries: dict[str, IsoEntry] = {}
        self._walk(int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little"), "")

    def close(self) -> None:
        self.f.close()

    def __enter__(self) -> "IsoReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _walk(self, lba: int, size: int, prefix: str) -> None:
        self.f.seek(lba * SECTOR)
        data = self.f.read(size)
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:  # registros não cruzam setor
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            rec = data[pos : pos + length]
            pos += length
            name = rec[33 : 33 + rec[32]]
            if name in (b"\x00", b"\x01"):
                continue
            entry = IsoEntry(
                path=prefix + name.decode("ascii", "replace").split(";")[0],
                lba=int.from_bytes(rec[2:6], "little"),
                size=int.from_bytes(rec[10:14], "little"),
                is_dir=bool(rec[25] & 2),
            )
            self.entries[entry.path.upper()] = entry
            if entry.is_dir:
                self._walk(entry.lba, entry.size, entry.path + "/")

    def files(self) -> list[IsoEntry]:
        return sorted((e for e in self.entries.values() if not e.is_dir), key=lambda e: e.lba)

    def find(self, path: str) -> IsoEntry:
        key = path.replace("\\", "/").lstrip("/").upper()
        if key not in self.entries:
            raise FileNotFoundError(f"{path} não existe em {self.path.name}")
        return self.entries[key]

    def read(self, path: str) -> bytes:
        e = self.find(path)
        self.f.seek(e.lba * SECTOR)
        return self.f.read(e.size)

    def extract(self, entry: IsoEntry, dest: Path, on_bytes: Callable[[int], None] | None = None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.f.seek(entry.lba * SECTOR)
        left = entry.size
        with open(dest, "wb") as out:
            while left > 0:
                chunk = self.f.read(min(left, 4 << 20))
                if not chunk:
                    raise IOError(f"ISO truncada ao ler {entry.path}")
                out.write(chunk)
                left -= len(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
