# godhand-recomp

Recomp estático do God Hand (PS2, SLUS-21503) para C++ nativo, usando o
[PS2Recomp](https://github.com/ran-j/PS2Recomp). A aplicação carrega a ISO, extrai, analisa o
executável no Ghidra, converte para C++ e compila um `.exe`. Não é um emulador nem um launcher.

**Nenhum arquivo do jogo, BIOS ou código gerado a partir do jogo fica neste repositório.**
O `.gitignore` é uma lista branca: só entra o que está explicitamente liberado.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `recomp-app/` | Aplicação (núcleo, CLI, GUI). Ver `recomp-app/README.md`. |
| `third_party/PS2Recomp/` | Subtree do PS2Recomp (GPL-3.0) com as alterações deste projeto. |
| `mods/` | Um diretório por mod; só o `mod.toml` é versionado. |
| `.github/workflows/` | `app` (testes da aplicação) e `tools` (build MSVC do PS2Recomp, release em tags). |

Ficam só na máquina local: a ISO, `bios/`, `work/` (extração, projeto Ghidra, C++ gerado, builds),
`refs/pcsx2/` (código de referência), `tools/` e `out/`.

## Versionamento

Versionamento semântico com tags `vX.Y.Z`; mudanças registradas em `CHANGELOG.md`. A versão da
aplicação fica em `recomp-app/recomp/__init__.py`.

Atualizar o PS2Recomp a partir do upstream:

    git subtree pull --prefix third_party/PS2Recomp https://github.com/ran-j/PS2Recomp.git main --squash

## Licença

O PS2Recomp é GPL-3.0; o código deste repositório que deriva dele segue a mesma licença.
