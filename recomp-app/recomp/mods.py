"""Patches, mods e texturas: três coisas separadas, cada uma na sua pasta, como no PCSX2.

    patches/    arquivos de patch: .xdelta (aplicado à ISO) e .pnach (patch de memória do PCSX2)
    mods/       uma pasta por mod, com arquivos que substituem os do disco
    textures/   uma pasta por pacote de texturas de substituição

Basta soltar o arquivo (ou a pasta) no lugar certo. Sem configuração, o item está ativo e entra em
ordem alfabética. Ao ativar, desativar ou reordenar, o app grava o estado em <pasta>/<pasta>.toml
(ex.: patches/patches.toml).
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

PATCH_TYPES = {".xdelta": "xdelta", ".vcdiff": "xdelta", ".pnach": "pnach"}
# Tipos que o pipeline já aplica. Os demais são listados, mas ainda não têm efeito.
APPLIED_TYPES = ("xdelta",)
DEFAULT_ORDER = 100


@dataclass
class Item:
    """Um patch, um mod ou um pacote de texturas."""

    root: Path  # pasta onde o item está (patches/, mods/ ou textures/)
    id: str  # nome do arquivo ou da pasta dentro de root
    name: str
    type: str  # xdelta | pnach | files | textures
    enabled: bool
    order: int

    @property
    def path(self) -> Path:
        return self.root / self.id

    @property
    def applied(self) -> bool:
        return self.type in APPLIED_TYPES


def _state_file(root: Path) -> Path:
    return root / f"{root.name}.toml"


def _read_state(root: Path) -> dict:
    state = _state_file(root)
    if not state.is_file():
        return {}
    with open(state, "rb") as f:
        return tomllib.load(f).get("items", {})


def _build(root: Path, found: list[tuple[str, str, str]]) -> list[Item]:
    state = _read_state(root)
    items = []
    for item_id, item_type, default_name in found:
        s = state.get(item_id, {})
        items.append(Item(
            root=root, id=item_id, type=item_type,
            name=str(s.get("name", default_name)),
            enabled=bool(s.get("enabled", True)),
            order=int(s.get("order", DEFAULT_ORDER)),
        ))
    return sorted(items, key=lambda i: (i.order, i.id))


def load_patches(root: Path) -> list[Item]:
    """Arquivos de patch soltos em patches/."""
    root.mkdir(parents=True, exist_ok=True)
    return _build(root, [
        (f.name, PATCH_TYPES[f.suffix.lower()], f.stem)
        for f in sorted(root.iterdir()) if f.is_file() and f.suffix.lower() in PATCH_TYPES
    ])


def _load_folders(root: Path, item_type: str) -> list[Item]:
    root.mkdir(parents=True, exist_ok=True)
    return _build(root, [(d.name, item_type, d.name) for d in sorted(root.iterdir()) if d.is_dir()])


def load_mods(root: Path) -> list[Item]:
    """Mods de substituição de arquivos: uma pasta por mod em mods/."""
    return _load_folders(root, "files")


def load_textures(root: Path) -> list[Item]:
    """Pacotes de texturas: uma pasta por pacote em textures/."""
    return _load_folders(root, "textures")


def save_item(item: Item) -> None:
    """Grava o estado do item no arquivo de estado da pasta dele, preservando as outras entradas."""
    state = _read_state(item.root)
    state[item.id] = {"name": item.name, "enabled": item.enabled, "order": item.order}
    q = lambda s: json.dumps(s, ensure_ascii=False)
    lines = ["# Estado gravado pelo app. Pode ser editado à mão.", ""]
    for item_id in sorted(state):
        entry = state[item_id]
        lines.append(f"[items.{q(item_id)}]")
        if "name" in entry:
            lines.append(f"name = {q(str(entry['name']))}")
        lines.append(f"enabled = {'true' if entry.get('enabled', True) else 'false'}")
        lines.append(f"order = {int(entry.get('order', DEFAULT_ORDER))}")
        lines.append("")
    _state_file(item.root).write_text("\n".join(lines), encoding="utf-8")
