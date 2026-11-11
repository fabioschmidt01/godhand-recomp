"""Linha de comando: python -m recomp <comando>."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_env
from .doctor import run_doctor
from .mods import load_mods
from .pipeline import STAGE_KEYS, STAGES, Context, Reporter, StageError, export_map, run_game, run_pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recomp", description="Recomp estático de PS2: ISO -> C++ -> exe nativo")
    ap.add_argument("command", choices=["gui", "doctor", "mods", "all", "run", "export-map", *[k.replace("_", "-") for k in STAGE_KEYS]],
                    help="gui abre a interface; all roda o pipeline inteiro; uma etapa roda até ela; run executa o jogo; export-map (desenvolvimento) gera o mapa de funções com o Ghidra")
    ap.add_argument("--seconds", type=float, help="com run: encerra o jogo após esse tempo")
    ap.add_argument("--version", action="version", version=f"recomp {__version__}")
    ap.add_argument("--iso", type=Path, help="ISO do jogo (padrão: a de env.toml)")
    ap.add_argument("--force", action="store_true", help="ignora o cache da etapa pedida")
    ap.add_argument("--force-all", action="store_true", help="ignora o cache de todas as etapas")
    args = ap.parse_args(argv)
    env = load_env()

    if args.command == "gui":
        from .gui import main as gui_main
        return gui_main()

    if args.command == "doctor":
        checks = run_doctor(env)
        for c in checks:
            mark = "OK  " if c.ok else ("FALTA" if c.required else "aviso")
            print(f"{mark:5} {c.name:36} {c.detail}")
        return 0 if all(c.ok or not c.required for c in checks) else 1

    if args.command == "mods":
        mods = load_mods(env.mods)
        if not mods:
            print(f"nenhum mod em {env.mods}")
        for m in mods:
            state = "ativo" if m.enabled else "desligado"
            note = "" if m.applied else "  (tipo ainda não aplicado pelo pipeline)"
            print(f"{m.order:4} {state:9} {m.type:8} {m.id:20} {m.name}{note}")
        return 0

    if args.command == "run":
        ctx = Context(env=env, iso=(args.iso or env.iso).resolve(), reporter=Reporter())
        try:
            code = run_game(ctx, args.seconds)
        except StageError as e:
            print(f"erro: {e}")
            return 1
        print("jogo encerrado pelo limite de tempo" if code is None else f"jogo terminou com código {code}")
        return 0 if code in (None, 0) else 1

    if args.command == "export-map":
        ctx = Context(env=env, iso=(args.iso or env.iso).resolve(), reporter=Reporter())
        try:
            print(export_map(ctx))
        except StageError as e:
            print(f"erro: {e}")
            return 1
        return 0

    until = "build" if args.command == "all" else args.command.replace("-", "_")
    force = set(STAGE_KEYS) if args.force_all else ({until} if args.force else set())
    ctx = Context(env=env, iso=(args.iso or env.iso).resolve(), reporter=Reporter(), force=force)
    titles = {k: t for k, t, _ in STAGES}
    print(f"Pipeline até: {titles[until]}")
    return 0 if run_pipeline(ctx, until) else 1


if __name__ == "__main__":
    sys.exit(main())
