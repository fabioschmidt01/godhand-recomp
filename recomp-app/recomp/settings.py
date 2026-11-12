"""Escolhas do usuário que a interface lembra entre execuções (ISO, BIOS). Fica em work/, fora do git."""
from __future__ import annotations

import json
from pathlib import Path

from .config import Env


def _file(env: Env) -> Path:
    return env.work / "settings.json"


def load_settings(env: Env) -> dict:
    try:
        return json.loads(_file(env).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(env: Env, **values: str) -> None:
    data = load_settings(env)
    data.update(values)
    env.work.mkdir(parents=True, exist_ok=True)
    _file(env).write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def bios_path(env: Env) -> Path | None:
    value = load_settings(env).get("bios")
    return Path(value) if value else None


def apply_to_env(env: Env) -> Env:
    """Aplica ao ambiente as opções de compilação escolhidas na interface (núcleos, otimização)."""
    import dataclasses

    data = load_settings(env)
    changes = {}
    if isinstance(data.get("jobs"), int) and data["jobs"] > 0:
        changes["jobs"] = data["jobs"]
    if data.get("opt_level") in ("-O1", "-O2"):
        changes["opt_level"] = data["opt_level"]
    return dataclasses.replace(env, **changes) if changes else env
