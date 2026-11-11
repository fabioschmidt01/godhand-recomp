"""Testes do núcleo com uma ISO sintética: nenhum arquivo de jogo é necessário."""
import dataclasses
import json
import struct
import tempfile
import unittest
from pathlib import Path

from recomp.config import load_env
from recomp.iso9660 import SECTOR, IsoReader
from recomp.mods import load_mods, save_mod
from recomp.pipeline import Context, Reporter, pcsx2_crc, run_pipeline


def _record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    length = 33 + len(name) + (1 - len(name) % 2)
    r = bytearray(length)
    r[0] = length
    r[2:6], r[6:10] = lba.to_bytes(4, "little"), lba.to_bytes(4, "big")
    r[10:14], r[14:18] = size.to_bytes(4, "little"), size.to_bytes(4, "big")
    r[25] = 2 if is_dir else 0
    r[32] = len(name)
    r[33 : 33 + len(name)] = name
    return bytes(r)


def make_iso(path: Path, files: dict[str, bytes]) -> dict[str, int]:
    """Gera uma ISO9660 mínima com arquivos na raiz e em uma subpasta MODULES. Devolve o LBA de cada arquivo."""
    root_lba, sub_lba, next_lba = 18, 19, 20
    lbas: dict[str, int] = {}
    root = _record(b"\x00", root_lba, SECTOR, True) + _record(b"\x01", root_lba, SECTOR, True)
    sub = _record(b"\x00", sub_lba, SECTOR, True) + _record(b"\x01", root_lba, SECTOR, True)
    root += _record(b"MODULES", sub_lba, SECTOR, True)
    for name, data in files.items():
        lbas[name] = next_lba
        rec = _record(name.split("/")[-1].encode() + b";1", next_lba, len(data), False)
        if name.startswith("MODULES/"):
            sub += rec
        else:
            root += rec
        next_lba += max(1, -(-len(data) // SECTOR))
    image = bytearray(next_lba * SECTOR)
    image[16 * SECTOR] = 1
    image[16 * SECTOR + 1 : 16 * SECTOR + 6] = b"CD001"
    image[16 * SECTOR + 156 : 16 * SECTOR + 190] = _record(b"\x00", root_lba, SECTOR, True)
    image[root_lba * SECTOR : root_lba * SECTOR + len(root)] = root
    image[sub_lba * SECTOR : sub_lba * SECTOR + len(sub)] = sub
    for name, data in files.items():
        image[lbas[name] * SECTOR : lbas[name] * SECTOR + len(data)] = data
    path.write_bytes(image)
    return lbas


FAKE_ELF = b"\x7fELF" + bytes(20) + struct.pack("<I", 0x00100008) + bytes(4068)
FILES = {
    "SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_215.03;1\r\nVER = 1.00\r\nVMODE = NTSC\r\n",
    "SLUS_215.03": FAKE_ELF,
    "MODULES/IRX.IMS": b"IMS\x00" + bytes(5000),
}


class IsoReaderTest(unittest.TestCase):
    def test_lists_and_reads_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso_path = Path(tmp) / "t.iso"
            lbas = make_iso(iso_path, FILES)
            with IsoReader(iso_path) as iso:
                self.assertEqual({e.path for e in iso.files()}, set(FILES))
                self.assertEqual(iso.read("system.cnf"), FILES["SYSTEM.CNF"])  # busca sem diferenciar caixa
                self.assertEqual(iso.find("MODULES/IRX.IMS").lba, lbas["MODULES/IRX.IMS"])
                self.assertEqual(iso.read("MODULES\\IRX.IMS"), FILES["MODULES/IRX.IMS"])

    def test_rejects_non_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.iso"
            bad.write_bytes(bytes(40 * SECTOR))
            with self.assertRaises(ValueError):
                IsoReader(bad)


class PipelineTest(unittest.TestCase):
    def test_ingest_and_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            iso_path = tmp / "game.iso"
            lbas = make_iso(iso_path, FILES)
            env = dataclasses.replace(load_env(), work=tmp / "work", mods=tmp / "mods", out=tmp / "out")
            ctx = Context(env=env, iso=iso_path, reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            self.assertTrue(run_pipeline(ctx, "extract"))
            self.assertEqual(ctx.game["serial"], "SLUS-21503")
            self.assertEqual(ctx.game["entry"], "0x00100008")
            self.assertEqual(ctx.effective_iso, iso_path)  # sem mods: usa a ISO original
            disc = env.work / "SLUS-21503" / "disc"
            self.assertEqual((disc / "MODULES" / "IRX.IMS").read_bytes(), FILES["MODULES/IRX.IMS"])
            self.assertEqual(ctx.elf.read_bytes(), FAKE_ELF)
            index = {e["path"]: e["lba"] for e in json.loads((disc / "index.json").read_text("utf-8"))}
            self.assertEqual(index, lbas)

            # segunda execução: entrada inalterada, extração vem do cache
            seen: list[tuple[str, str]] = []
            ctx2 = Context(env=env, iso=iso_path, reporter=Reporter())
            ctx2.reporter.log = lambda msg: None
            ctx2.reporter.stage = lambda key, status, detail="": seen.append((key, status))
            self.assertTrue(run_pipeline(ctx2, "extract"))
            self.assertIn(("extract", "cached"), seen)

    def test_missing_iso_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            env = dataclasses.replace(load_env(), work=tmp / "work", mods=tmp / "mods")
            seen: list[tuple[str, str]] = []
            ctx = Context(env=env, iso=tmp / "nao-existe.iso", reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            ctx.reporter.stage = lambda key, status, detail="": seen.append((key, status))
            self.assertFalse(run_pipeline(ctx, "extract"))
            self.assertEqual(seen[-1], ("ingest", "error"))

    def test_pcsx2_crc_is_xor_of_words(self):
        self.assertEqual(pcsx2_crc(struct.pack("<III", 0x11111111, 0x22222222, 0x44444444)), "77777777")


class ModsTest(unittest.TestCase):
    def test_roundtrip_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods_dir = Path(tmp)
            for mid, order in (("b", 20), ("a", 10)):
                (mods_dir / mid).mkdir()
                (mods_dir / mid / "mod.toml").write_text(
                    f'name = "Mod {mid}"\ntype = "xdelta"\nenabled = true\norder = {order}\nfile = "p.xdelta"\n', "utf-8")
            (mods_dir / "sem-manifesto").mkdir()
            mods = load_mods(mods_dir)
            self.assertEqual([m.id for m in mods], ["a", "b"])
            mods[0].enabled = False
            save_mod(mods[0])
            again = load_mods(mods_dir)
            self.assertFalse(again[0].enabled)
            self.assertEqual(again[0].file, "p.xdelta")


if __name__ == "__main__":
    unittest.main()
