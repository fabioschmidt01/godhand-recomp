"""Descoberta de mods: uma pasta por mod em mods/, com um mod.toml.

    name = "Uncensor Patch"
    type = "xdelta"        # xdelta | pnach | files | textures
    enabled = true
    order = 10
    file = "patch.xdelta"  # xdelta e pnach
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

TYPES = ("xdelta", "pnach", "files", "textures")
# Tipos que o pipeline já aplica. Os demais são listados, mas ainda não têm efeito.
APPLIED_TYPES = ("xdelta",)


@dataclass
class Mod:
    dir: Path
    name: str
    type: str
    enabled: bool
    order: int
    file: str

    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def path(self) -> Path:
        return self.dir / self.file if self.file else self.dir

    @property
    def applied(self) -> bool:
        return self.type in APPLIED_TYPES


def load_mods(mods_dir: Path) -> list[Mod]:
    mods: list[Mod] = []
    if not mods_dir.is_dir():
        return mods
    for d in sorted(p for p in mods_dir.iterdir() if p.is_dir()):
        manifest = d / "mod.toml"
        if not manifest.is_file():
            continue
        with open(manifest, "rb") as f:
            t = tomllib.load(f)
        mods.append(
            Mod(
                dir=d,
                name=str(t.get("name", d.name)),
                type=str(t.get("type", "files")),
                enabled=bool(t.get("enabled", True)),
                order=int(t.get("order", 100)),
                file=str(t.get("file", "")),
            )
        )
    return sorted(mods, key=lambda m: (m.order, m.id))


def save_mod(mod: Mod) -> None:
    q = lambda s: json.dumps(s, ensure_ascii=False)
    lines = [
        f"name = {q(mod.name)}",
        f"type = {q(mod.type)}",
        f"enabled = {'true' if mod.enabled else 'false'}",
        f"order = {mod.order}",
    ]
    if mod.file:
        lines.append(f"file = {q(mod.file)}")
    (mod.dir / "mod.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
