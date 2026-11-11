"""Testes do núcleo com uma ISO sintética: nenhum arquivo de jogo é necessário."""
import dataclasses
import hashlib
import json
import struct
import tempfile
import tomllib
import unittest
from pathlib import Path

from recomp.config import load_env
from recomp.iso9660 import SECTOR, IsoReader
from recomp.mods import load_mods, load_patches, load_textures, save_item
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
            env = dataclasses.replace(load_env(), work=tmp / "work", patches=tmp / "patches", mods=tmp / "mods", textures=tmp / "textures", out=tmp / "out")
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
            env = dataclasses.replace(load_env(), work=tmp / "work", patches=tmp / "patches", mods=tmp / "mods", textures=tmp / "textures")
            seen: list[tuple[str, str]] = []
            ctx = Context(env=env, iso=tmp / "nao-existe.iso", reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            ctx.reporter.stage = lambda key, status, detail="": seen.append((key, status))
            self.assertFalse(run_pipeline(ctx, "extract"))
            self.assertEqual(seen[-1], ("ingest", "error"))

    def test_cancel_stops_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            iso_path = tmp / "game.iso"
            make_iso(iso_path, FILES)
            env = dataclasses.replace(load_env(), work=tmp / "work", patches=tmp / "patches", mods=tmp / "mods", textures=tmp / "textures")
            seen: list[tuple[str, str, str]] = []
            ctx = Context(env=env, iso=iso_path, reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            ctx.reporter.stage = lambda key, status, detail="": seen.append((key, status, detail))
            ctx.cancel()
            self.assertFalse(run_pipeline(ctx, "extract"))
            self.assertEqual(seen[-1][:2], ("ingest", "error"))
            self.assertIn("cancelado", seen[-1][2])
            self.assertFalse((env.work / "SLUS-21503").exists())

    def _env_without_ghidra(self, tmp: Path):
        return dataclasses.replace(load_env(), work=tmp / "work", patches=tmp / "patches", mods=tmp / "mods", textures=tmp / "textures", games=tmp / "games",
                                   ghidra=tmp / "sem-ghidra")

    def test_function_map_bundled_with_app_needs_no_ghidra(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            iso_path = tmp / "game.iso"
            make_iso(iso_path, FILES)
            env = self._env_without_ghidra(tmp)
            gdir = env.games / "SLUS-21503"
            gdir.mkdir(parents=True)
            (gdir / "game.toml").write_text(f'elf_sha1 = "{hashlib.sha1(FAKE_ELF).hexdigest()}"\n', "utf-8")
            (gdir / "functions.csv").write_text("Name,Start,End,Size\nentry,0x00100008,0x00100248,576\n", "utf-8")
            (gdir / "recomp.toml").write_text(
                '[general]\ninput = "@INPUT@"\noutput = "@OUTPUT@"\nghidra_output = "@GHIDRA_OUTPUT@"\nstubs = []\n', "utf-8")
            seen: list[tuple[str, str, str]] = []
            ctx = Context(env=env, iso=iso_path, reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            ctx.reporter.stage = lambda key, status, detail="": seen.append((key, status, detail))
            self.assertTrue(run_pipeline(ctx, "analyze"), seen)
            self.assertIn("mapa incluído no app: 1 ", seen[-1][2])
            with open(ctx.gen / "config.toml", "rb") as f:
                general = tomllib.load(f)["general"]
            self.assertEqual(Path(general["input"]), ctx.elf)
            self.assertEqual(Path(general["output"]), ctx.gen / "output")
            self.assertEqual(Path(general["ghidra_output"]), ctx.gen / "functions.csv")
            self.assertEqual(general["stubs"], [])
            self.assertEqual((ctx.gen / "functions.csv").read_text("utf-8"), (gdir / "functions.csv").read_text("utf-8"))

    def test_unknown_executable_without_ghidra_explains_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            iso_path = tmp / "game.iso"
            make_iso(iso_path, FILES)
            env = self._env_without_ghidra(tmp)
            gdir = env.games / "SLUS-21503"  # mapa de outra versão do executável: não pode ser usado
            gdir.mkdir(parents=True)
            (gdir / "game.toml").write_text('elf_sha1 = "0000"\n', "utf-8")
            (gdir / "functions.csv").write_text("Name,Start,End,Size\n", "utf-8")
            (gdir / "recomp.toml").write_text('input = ""\noutput = ""\nghidra_output = ""\n', "utf-8")
            seen: list[tuple[str, str, str]] = []
            ctx = Context(env=env, iso=iso_path, reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            ctx.reporter.stage = lambda key, status, detail="": seen.append((key, status, detail))
            self.assertFalse(run_pipeline(ctx, "analyze"))
            self.assertEqual(seen[-1][:2], ("analyze", "error"))
            self.assertIn("não tem mapa de funções", seen[-1][2])
            self.assertFalse((ctx.gen / "config.toml").exists())

    def test_pcsx2_crc_is_xor_of_words(self):
        self.assertEqual(pcsx2_crc(struct.pack("<III", 0x11111111, 0x22222222, 0x44444444)), "77777777")


class PatchesAndModsTest(unittest.TestCase):
    def test_patches_are_files_dropped_in_the_patches_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "patches"
            self.assertEqual(load_patches(root), [])  # cria a pasta
            (root / "b-uncensor.xdelta").write_bytes(b"x")
            (root / "a-widescreen.pnach").write_text("patch=1", "utf-8")
            (root / "leia-me.txt").write_text("ignorado", "utf-8")
            (root / "uma-pasta").mkdir()
            patches = load_patches(root)
            self.assertEqual([(p.id, p.type) for p in patches],
                             [("a-widescreen.pnach", "pnach"), ("b-uncensor.xdelta", "xdelta")])
            self.assertTrue(all(p.enabled for p in patches))  # sem estado gravado: ativo
            self.assertEqual(patches[1].path, root / "b-uncensor.xdelta")
            self.assertTrue(patches[1].applied)
            self.assertFalse(patches[0].applied)  # pnach ainda não é aplicado

    def test_mods_and_textures_are_folders_and_never_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "mods" / "novo-modelo").mkdir(parents=True)
            (tmp / "mods" / "solto.xdelta").write_bytes(b"x")  # patch na pasta errada não vira mod
            (tmp / "textures" / "hd-pack").mkdir(parents=True)
            self.assertEqual([(m.id, m.type) for m in load_mods(tmp / "mods")], [("novo-modelo", "files")])
            self.assertEqual([(t.id, t.type) for t in load_textures(tmp / "textures")], [("hd-pack", "textures")])
            self.assertEqual(load_patches(tmp / "patches"), [])

    def test_state_roundtrip_keeps_other_entries_and_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "patches"
            load_patches(root)
            for name in ("a.xdelta", "b.xdelta"):
                (root / name).write_bytes(b"x")
            a, b = load_patches(root)
            b.order, b.name = 10, "Patch B"
            save_item(b)
            a.enabled = False
            save_item(a)
            self.assertTrue((root / "patches.toml").is_file())
            again = load_patches(root)
            self.assertEqual([p.id for p in again], ["b.xdelta", "a.xdelta"])  # ordem 10 antes de 100
            self.assertEqual(again[0].name, "Patch B")
            self.assertTrue(again[0].enabled)
            self.assertFalse(again[1].enabled)

    def test_disabled_patch_is_not_applied_to_the_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            iso_path = tmp / "game.iso"
            make_iso(iso_path, FILES)
            env = dataclasses.replace(load_env(), work=tmp / "work", patches=tmp / "patches",
                                      mods=tmp / "mods", textures=tmp / "textures")
            env.patches.mkdir()
            (env.patches / "quebrado.xdelta").write_bytes(b"isto nao e um xdelta")
            patch = load_patches(env.patches)[0]
            patch.enabled = False
            save_item(patch)
            ctx = Context(env=env, iso=iso_path, reporter=Reporter())
            ctx.reporter.log = lambda msg: None
            self.assertTrue(run_pipeline(ctx, "patch_iso"))
            self.assertEqual(ctx.effective_iso, iso_path)  # patch desligado: ISO original, sem chamar o xdelta3


if __name__ == "__main__":
    unittest.main()
