"""Confere os dados por jogo versionados em games/ (mapas de funções gerados no desenvolvimento)."""
import csv
import tomllib
import unittest
from pathlib import Path

GAMES = Path(__file__).resolve().parent.parent / "games"


class BundledMapsTest(unittest.TestCase):
    def test_every_game_folder_is_complete(self):
        folders = [d for d in GAMES.iterdir() if d.is_dir()]
        self.assertTrue(folders, "nenhum jogo em games/")
        for d in folders:
            with self.subTest(game=d.name):
                for name in ("game.toml", "functions.csv", "recomp.toml"):
                    self.assertTrue((d / name).is_file(), f"falta {name}")
                with open(d / "game.toml", "rb") as f:
                    meta = tomllib.load(f)
                self.assertEqual(meta["serial"], d.name)
                self.assertRegex(meta["elf_sha1"], r"^[0-9a-f]{40}$")
                template = (d / "recomp.toml").read_text("utf-8")
                for key in ("@INPUT@", "@OUTPUT@", "@GHIDRA_OUTPUT@"):
                    self.assertIn(key, template)  # caminhos são preenchidos na máquina de quem usa
                self.assertNotRegex(template, r"[A-Za-z]:[\\/]")  # nenhum caminho local versionado

    def test_function_maps_are_consistent(self):
        for d in (p for p in GAMES.iterdir() if p.is_dir()):
            with self.subTest(game=d.name):
                with open(d / "game.toml", "rb") as f:
                    meta = tomllib.load(f)
                with open(d / "functions.csv", encoding="utf-8", newline="") as f:
                    rows = list(csv.DictReader(f))
                self.assertEqual(len(rows), meta["entries"])
                starts = [int(r["Start"], 16) for r in rows]
                self.assertEqual(len(starts), len(set(starts)), "endereços de início repetidos")
                self.assertIn(int(meta["entry"], 16), starts, "o ponto de entrada do ELF precisa estar no mapa")
                for r in rows:
                    start, end, size = int(r["Start"], 16), int(r["End"], 16), int(r["Size"])
                    # Regressão: o exportador já gravou como fim o endereço mais alto de um corpo
                    # não contíguo, o que fazia uma função de 140 bytes cobrir 279 KB.
                    self.assertEqual(end - start, size, f"{r['Name']}: fim não bate com o tamanho")
                    self.assertGreater(size, 0, r["Name"])
                    self.assertEqual(start % 4, 0, r["Name"])


if __name__ == "__main__":
    unittest.main()
