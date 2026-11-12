"""BIOS do PS2 fornecida pelo usuário: validação e identificação (nunca distribuída, nunca enviada)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

REGIONS = {"J": "Japão", "A": "EUA", "E": "Europa", "H": "Ásia", "C": "China", "T": "T10K", "X": "QA"}
WHY_TEXT = (
    "Por que a BIOS? O código do God Hand é traduzido para C++ e compilado como um programa nativo do Windows: "
    "o jogo não roda num emulador. Mas o PS2 tem um segundo processador, o IOP, que cuida do som, do controle, "
    "do cartão de memória e da leitura do disco, e o sistema desse processador está gravado na BIOS do console. "
    "A BIOS é da Sony e não pode ser distribuída, por isso o app usa a do seu próprio PS2, só para essa parte "
    "(os módulos do IOP rodam num interpretador, como nos outros ports por recompilação). "
    "O arquivo é só lido: nunca é alterado nem enviado para lugar nenhum."
)


@dataclass
class BiosInfo:
    path: Path
    version: str  # ex.: "2.30"
    region: str  # ex.: "EUA"
    date: str  # ex.: "2008-02-20"
    romver: str

    def describe(self) -> str:
        return f"BIOS v{self.version}, {self.region}, {self.date}"


class BiosError(ValueError):
    pass


def identify_bios(path: Path) -> BiosInfo:
    """Confere que o arquivo é a ROM principal da BIOS do PS2 e lê o ROMVER (versão, região, data)."""
    path = Path(path)
    if not path.is_file():
        raise BiosError(f"arquivo não encontrado: {path}")
    size = path.stat().st_size
    if size not in (4 << 20, 8 << 20):
        raise BiosError(f"{path.name} tem {size:,} bytes; a BIOS do PS2 tem 4 MB (às vezes 8 MB)")
    data = path.read_bytes()
    start = data.find(b"RESET\0\0\0\0\0")
    if start < 0 or data[start + 16:start + 22] != b"ROMDIR":
        raise BiosError(f"{path.name} não parece a BIOS principal do PS2 (falta o diretório ROMDIR)")
    offset, pos = 0, start
    while pos + 16 <= len(data):
        name = data[pos:pos + 10].split(b"\0")[0]
        if not name:
            break
        _ext, fsize = struct.unpack_from("<HI", data, pos + 10)
        if name == b"ROMVER":
            romver = data[offset:offset + fsize].split(b"\0")[0].strip().decode("ascii", "replace")
            if len(romver) < 14 or not romver[:4].isdigit():
                raise BiosError(f"ROMVER inesperado em {path.name}: {romver!r}")
            return BiosInfo(
                path=path,
                version=f"{int(romver[0:2])}.{romver[2:4]}",
                region=REGIONS.get(romver[4], f"região {romver[4]}"),
                date=f"{romver[6:10]}-{romver[10:12]}-{romver[12:14]}",
                romver=romver,
            )
        offset += (fsize + 15) & ~15
        pos += 16
    raise BiosError(f"{path.name} não tem o arquivo ROMVER: não é uma BIOS do PS2 reconhecível")
