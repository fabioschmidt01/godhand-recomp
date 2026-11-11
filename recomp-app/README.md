# recomp-app

Aplicação (GUI + CLI) que leva uma ISO de PS2 até um executável nativo em C++ usando o
[PS2Recomp](https://github.com/ran-j/PS2Recomp). Foco atual: God Hand (SLUS-21503).

    recomp-gui.bat            abre a interface
    recomp.bat doctor         verifica o ambiente
    recomp.bat mods           lista os mods
    recomp.bat all            roda o pipeline inteiro
    recomp.bat <etapa>        roda até a etapa: ingest, patch-iso, extract, analyze, recompile, build
    recomp.bat <etapa> --force   ignora o cache da etapa

Caminhos de ferramentas ficam em `env.toml`. Arquivos do jogo e BIOS nunca entram neste repositório.
