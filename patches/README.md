# patches

Solte aqui os arquivos de patch; o app reconhece sozinho (aba **Patches** ou `recomp.bat patches`).

| Tipo | O que faz | Situação |
|---|---|---|
| `.xdelta` | patch aplicado à ISO antes da extração (ex.: remoção de censura, tradução) | aplicado |
| `.pnach` | patch de memória no formato do PCSX2 (widescreen, 60 fps, cheats) | listado, ainda sem efeito |

Sem configuração, todo patch encontrado está ativo e entra em ordem alfabética. Ao ativar, desativar ou
reordenar pela interface, o app grava o estado em `patches.toml`.

Patch não é mod: mods (substituição de arquivos) ficam em `../mods/` e texturas em `../textures/`.

Só este arquivo é versionado. Arquivos de patch nunca vão para o repositório.
