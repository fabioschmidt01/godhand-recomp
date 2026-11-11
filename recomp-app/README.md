# recomp-app

Aplicação (GUI + CLI) que leva uma ISO de PS2 até um executável nativo em C++ usando o
[PS2Recomp](https://github.com/ran-j/PS2Recomp). Foco atual: God Hand (SLUS-21503).

## Uso

    recomp-gui.bat               abre a interface
    recomp.bat doctor            verifica o ambiente
    recomp.bat mods              lista os mods
    recomp.bat all               roda o pipeline inteiro
    recomp.bat <etapa>           roda até a etapa: ingest, patch-iso, extract, analyze, recompile, build
    recomp.bat <etapa> --force   ignora o cache da etapa
    recomp.bat run               executa o jogo compilado (--seconds N encerra após N segundos)

## Etapas

1. **ingest** identifica a ISO (serial, executável, hashes).
2. **patch-iso** aplica os mods xdelta ativos numa cópia da ISO.
3. **extract** extrai o disco para `work/<serial>/disc/`.
4. **analyze** instala o mapa de funções do executável, que vem pronto em `games/<serial>/`.
5. **recompile** converte o executável para C++ com `ps2_recomp`.
6. **build** compila o executável nativo.

## Configuração

`env.toml` tem os padrões. Para caminhos da sua máquina, copie `env.local.example.toml` para
`env.local.toml` (não versionado) e ajuste.

## Desenvolvimento

`games/<serial>/` guarda o que foi obtido por engenharia reversa e é versionado junto com o app:
`functions.csv` e `recomp.toml` (mapa de funções), `game.toml` (a qual executável o mapa pertence) e,
futuramente, `src/` com código escrito à mão para o jogo.

    recomp.bat export-map        gera o mapa de funções com o Ghidra (requer Ghidra + extensão R5900 + JDK 21)

Testes: `python -m unittest discover -s tests` (a GUI roda off-screen).

Arquivos do jogo, BIOS e patches nunca entram neste repositório.
