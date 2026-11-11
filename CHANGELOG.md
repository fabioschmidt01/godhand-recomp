# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), com
[versionamento semântico](https://semver.org/lang/pt-BR/). Cada versão recebe uma tag `vX.Y.Z`.

Enquanto o jogo não roda, as versões são `0.x`: `0.MINOR` muda a cada fase concluída do roadmap,
`PATCH` para correções.

## [Não lançado]

### Alterado
- A etapa "Analisar ELF no Ghidra" virou "Mapa de funções do executável": o mapa vem incluído no app em
  `recomp-app/games/<serial>/`, reconhecido pelo SHA-1 do executável original. Quem usa o app não precisa
  mais de Ghidra nem de Java. Ghidra, extensão R5900 e JDK passam a ser opcionais na checagem de ambiente.
- `env.toml` agora só tem padrões portáveis; caminhos da máquina vão em `env.local.toml` (não versionado).
- GitHub Actions: os três workflows (`app`, `tools`, `guard`) viraram um só, `build`, que entrega um pacote
  único: o app com o `ps2_recomp.exe` embutido em `recomp-app/bin/`, os mapas de funções e os fontes do runtime.
  A release deixa de ser um zip só de ferramentas.
- `recomp.bat` e `recomp-gui.bat` preparam o ambiente Python sozinhos na primeira execução.

### Adicionado
- Mapa de funções do God Hand USA (`SLUS_215.03`, 62.916 entradas) em `recomp-app/games/SLUS-21503/`.
- `recomp export-map` (desenvolvimento): gera o mapa de funções do executável original com o Ghidra.
- `recomp run` e botão "Executar jogo": abre o executável compilado apontando para o disco extraído,
  com a saída em `work/logs/run.log`.
- PS2Recomp: opções `PS2X_RUNNER_SOURCE_DIR` e `PS2X_RUNNER_EXTRA_SOURCE_DIR` no CMake do runtime, para
  compilar o C++ gerado direto de `work/` sem copiá-lo para o repositório.
- `env.toml`: `opt_level` e `unity_batch` para o build do código gerado.

### Corrigido
- PS2Recomp: objetos grandes (`-Wa,-mbig-obj` no MinGW, `/bigobj` no MSVC) no runner; o
  `register_functions.cpp` de um jogo inteiro passa do limite de 32 mil seções do COFF.

## [0.1.0] - 2026-11-11

### Adicionado
- Aplicação `recomp-app` com núcleo único usado pela CLI (`recomp.bat`) e pela GUI (`recomp-gui.bat`).
- Etapas do pipeline com cache por etapa: identificar ISO, aplicar patches xdelta, extrair disco,
  analisar o ELF no Ghidra (linguagem R5900), converter para C++ com `ps2_recomp`, compilar o executável.
- Checagem de ambiente (`recomp doctor`) e sistema de mods por pasta com `mod.toml` (xdelta aplicado no build).
- PS2Recomp incluído em `third_party/PS2Recomp` como subtree do upstream.
- Botão "Cancelar" na GUI: interrompe a etapa em execução e o processo externo dela.
- GitHub Actions: `app` (testes do núcleo, CLI e GUI), `tools` (PS2Recomp com MSVC + testes; release em tags)
  e `guard` (falha se ISO, dados do disco, BIOS ou patches forem versionados).

### Corrigido
- GUI: fechar a janela com uma etapa ou a checagem de ambiente em execução derrubava o processo.
- PS2Recomp: opção de pilha do linker de `ps2x_tests` no MinGW sem o prefixo `-Wl,`.
