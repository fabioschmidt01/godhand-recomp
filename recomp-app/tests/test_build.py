"""Testes do que mantém o build incremental e do mapa com funções do SDK."""
import os
import struct
import tempfile
import tomllib
import unittest
from pathlib import Path

from recomp.pipeline import (JR_RA, _merge_sdk_stubs, drop_data_entries, elf_code_section, sync_generated,
                             write_unity_buckets)

OLD = 1_600_000_000  # data antiga, para notar se um arquivo foi tocado


def _age(path: Path) -> None:
    os.utime(path, (OLD, OLD))


class SyncGeneratedTest(unittest.TestCase):
    def test_only_changed_files_are_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, new = Path(tmp) / "output", Path(tmp) / "output.new"
            out.mkdir()
            new.mkdir()
            for name, text in (("igual.cpp", "a"), ("muda.cpp", "b"), ("some.cpp", "c")):
                (out / name).write_text(text, "utf-8")
                _age(out / name)
            for name, text in (("igual.cpp", "a"), ("muda.cpp", "B"), ("novo.cpp", "d")):
                (new / name).write_text(text, "utf-8")
            self.assertEqual(sync_generated(new, out), (2, 1))  # muda + novo alterados, some removido
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["igual.cpp", "muda.cpp", "novo.cpp"])
            self.assertEqual((out / "muda.cpp").read_text("utf-8"), "B")
            self.assertEqual(int((out / "igual.cpp").stat().st_mtime), OLD)  # igual: data preservada
            self.assertNotEqual(int((out / "muda.cpp").stat().st_mtime), OLD)


class UnityBucketsTest(unittest.TestCase):
    def test_groups_by_address_and_rename_rewrites_one_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, unity = Path(tmp) / "output", Path(tmp) / "unity"
            out.mkdir()
            for name in ("FUN_00100000_0x100000.cpp", "FUN_00100400_0x100400.cpp", "FUN_00100800_0x100800.cpp",
                         "register_functions.cpp"):
                (out / name).write_text("// " + name, "utf-8")
            (out / "ps2_recompiled_functions.h").write_text("// h", "utf-8")
            self.assertEqual(write_unity_buckets(out, unity, 11), (3, 3))
            units = sorted(p.name for p in unity.glob("*.cpp"))
            self.assertEqual(units, ["u_000200.cpp", "u_000201.cpp", "x_register_functions.cpp"])
            first = (unity / "u_000200.cpp").read_text("utf-8")
            self.assertIn("FUN_00100000_0x100000.cpp", first)  # 0x100000 e 0x100400 caem na mesma faixa de 2 KB
            self.assertIn("FUN_00100400_0x100400.cpp", first)
            self.assertNotIn("FUN_00100800", first)
            self.assertTrue((unity / "ps2_recompiled_functions.h").is_file())

            for p in unity.glob("*.cpp"):
                _age(p)
            self.assertEqual(write_unity_buckets(out, unity, 11), (3, 0))  # nada mudou: nada reescrito

            # Uma função passa a ter nome do SDK: só a unidade da faixa dela é reescrita.
            (out / "FUN_00100400_0x100400.cpp").rename(out / "sceFoo_0x100400.cpp")
            self.assertEqual(write_unity_buckets(out, unity, 11), (3, 1))
            self.assertIn("sceFoo_0x100400.cpp", (unity / "u_000200.cpp").read_text("utf-8"))
            self.assertEqual(int((unity / "u_000201.cpp").stat().st_mtime), OLD)
            self.assertEqual(int((unity / "x_register_functions.cpp").stat().st_mtime), OLD)

            (out / "FUN_00100800_0x100800.cpp").unlink()  # função some: a unidade dela também
            self.assertEqual(write_unity_buckets(out, unity, 11), (2, 0))
            self.assertFalse((unity / "u_000201.cpp").exists())


class SdkStubsTest(unittest.TestCase):
    def test_sdk_functions_are_merged_into_the_stub_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk.toml"
            sdk.write_text('[general]\nstubs = ["sceCdRead@0x00399728", "Outro@0x003AF8A0", "printf"]\n', "utf-8")
            template = ('[general]\ninput = "@INPUT@"\nstubs = [\n  "SetGsCrt@0x003AF8A0",\n]\n'
                        'untracked_stubs = [\n]\nskip = []\n')
            text, n_base, n_sdk, n_manual = _merge_sdk_stubs(template, sdk)
            self.assertEqual((n_base, n_sdk, n_manual), (1, 1, 0))  # o endereço repetido fica com o nome do Ghidra
            general = tomllib.loads(text)["general"]
            self.assertEqual(general["stubs"], ["sceCdRead@0x00399728", "SetGsCrt@0x003AF8A0"])  # por endereço
            self.assertEqual(general["untracked_stubs"], [])
            self.assertEqual(general["input"], "@INPUT@")

    def test_manual_bindings_override_automatic_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "sdk.toml"
            sdk.write_text("\n".join(["[general]", 'stubs = ["sceCdRead@0x00399728"]', ""]), "utf-8")
            manual = Path(tmp) / "stubs.toml"
            manual.write_text("\n".join([
                "stubs = [",
                '  "sceCdSearchFile@0x00398890",  # achada à mão',
                '  "ret0@0x00399728",',
                "]", ""]), "utf-8")
            template = "\n".join(["[general]", "stubs = [", '  "SetGsCrt@0x003AF8A0",', "]", "skip = []", ""])
            text, n_base, n_sdk, n_manual = _merge_sdk_stubs(template, sdk, manual)
            self.assertEqual((n_base, n_sdk, n_manual), (1, 1, 2))
            self.assertEqual(tomllib.loads(text)["general"]["stubs"],
                             ["sceCdSearchFile@0x00398890", "ret0@0x00399728", "SetGsCrt@0x003AF8A0"])


def _tiny_elf(payload: bytes, base: int, text_size: int) -> bytes:
    """ELF mínimo: um segmento carregável com `payload` em `base` e duas seções executáveis (.text e .data)."""
    header = bytearray(52)
    header[:4] = b"\x7fELF"
    ph_off, data_off = 52, 0x100
    sh_off = data_off + len(payload)
    struct.pack_into("<I", header, 24, base)  # e_entry
    struct.pack_into("<I", header, 28, ph_off)
    struct.pack_into("<I", header, 32, sh_off)
    struct.pack_into("<H", header, 44, 1)  # e_phnum
    struct.pack_into("<H", header, 48, 2)  # e_shnum
    phdr = struct.pack("<IIIIIIII", 1, data_off, base, base, len(payload), len(payload), 7, 0x10)
    text = struct.pack("<IIIIIIIIII", 0, 1, 6, base, data_off, text_size, 0, 0, 4, 0)
    data = struct.pack("<IIIIIIIIII", 0, 1, 7, base + text_size, data_off + text_size, len(payload) - text_size, 0, 0, 4, 0)
    return bytes(header) + phdr + bytes(data_off - 52 - len(phdr)) + payload + text + data


class DropDataEntriesTest(unittest.TestCase):
    def test_data_tables_are_dropped_but_real_code_outside_text_is_kept(self):
        base, text_size = 0x100000, 0x100
        table = bytes(0x800)  # tabela de dados: 2 KB sem nenhum jr ra
        code = struct.pack("<I", 0x27BDFFE0) + bytes(0x3F8) + struct.pack("<I", JR_RA)  # rotina real fora da .text
        payload = struct.pack("<I", JR_RA) * (text_size // 4) + table + code
        elf = _tiny_elf(payload, base, text_size)
        self.assertEqual(elf_code_section(elf), (base, base + text_size))
        t, c = base + text_size, base + text_size + len(table)
        rows = [
            ["entry", f"0x{base:08X}", f"0x{base + text_size:08X}", str(text_size)],
            ["entry_tabela", f"0x{t:08X}", f"0x{t + len(table):08X}", str(len(table))],  # dado: sai
            ["entry_codigo", f"0x{c:08X}", f"0x{c + len(code):08X}", str(len(code))],  # tem jr ra: fica
            ["FUN_grande_sem_ret", f"0x{t:08X}", f"0x{t + len(table):08X}", str(len(table))],  # função do Ghidra: fica
            ["entry_pequena", f"0x{t + 8:08X}", f"0x{t + 16:08X}", "8"],  # pequena demais para julgar: fica
        ]
        kept, dropped = drop_data_entries(rows, elf)
        self.assertEqual(dropped, ["entry_tabela"])
        self.assertEqual([r[0] for r in kept], ["entry", "entry_codigo", "FUN_grande_sem_ret", "entry_pequena"])


if __name__ == "__main__":
    unittest.main()
