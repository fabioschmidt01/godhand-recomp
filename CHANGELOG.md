# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), com
[versionamento semântico](https://semver.org/lang/pt-BR/). Cada versão recebe uma tag `vX.Y.Z`.

Enquanto o jogo não roda, as versões são `0.x`: `0.MINOR` muda a cada fase concluída do roadmap,
`PATCH` para correções.

## [Não lançado]

> **Ainda não validado com um build do jogo:** a aplicação das ligações manuais em runtime e o mapa sem as
> duas tabelas de dados. Os testes unitários passam; o próximo build do jogo confirma.

### Alterado
- O mapa de funções inclui também as funções do SDK da Sony reconhecidas por assinatura pelo `ps2_analyzer`
  (127 no God Hand, como `sceSifInitRpc`, `sceCdRead`, `scePad*`, `sceGs*`), ligadas às implementações nativas do
  runtime. Sem elas o jogo travava no boot esperando o IOP. `recomp export-map` reaproveita a análise do Ghidra já
  feita (`--force` refaz).
- O `export-map` descarta entradas do mapa que são tabelas de dados e não código (no God Hand, duas "funções" de
  26 KB e 229 KB na `.data`).
- Build incremental: a conversão só toca nos `.cpp` que mudaram, e o código é agrupado em unidades de compilação por
  faixa de endereço (`bucket_shift` no `env.toml`); mudar uma função recompila só a unidade dela.
- A etapa "Analisar ELF no Ghidra" virou "Mapa de funções do executável": o mapa vem incluído no app em
  `recomp-app/games/<serial>/`, reconhecido pelo SHA-1 do executável original. Quem usa o app não precisa
  mais de Ghidra nem de Java. Ghidra, extensão R5900 e JDK passam a ser opcionais na checagem de ambiente.
- `env.toml` agora só tem padrões portáveis; caminhos da máquina vão em `env.local.toml` (não versionado).
- Patches e mods agora são coisas separadas, cada um na sua pasta, como no PCSX2: `patches/` para arquivos
  `.xdelta` e `.pnach`, `mods/` para substituição de arquivos (uma pasta por mod) e `textures/` para pacotes
  de texturas. Basta soltar o arquivo na pasta; o `mod.toml` por item deixou de existir. O estado (ativo,
  ordem, nome) fica em `<pasta>/<pasta>.toml`, gravado pelo app e não versionado. Na interface, as abas
  **Patches** e **Mods** são separadas; na CLI, `recomp patches` e `recomp mods`.

### Adicionado
- `games/<serial>/stubs.toml`: funções do SDK identificadas à mão, ligadas ao handler nativo em runtime por um
  fonte gerado no build (acrescentar uma ligação recompila só esse fonte). Primeira entrada: `sceCdSearchFile`.
- Mapa de funções do God Hand USA (`SLUS_215.03`, 62.916 entradas) em `recomp-app/games/SLUS-21503/`.
- `recomp export-map` (desenvolvimento): gera o mapa de funções do executável original com o Ghidra.
- Teste de consistência dos mapas de funções versionados (`tests/test_games.py`).
- `recomp run` e botão "Executar jogo": abre o executável compilado apontando para o disco extraído,
  com a saída em `work/logs/run.log`.
- PS2Recomp: opções `PS2X_RUNNER_SOURCE_DIR` e `PS2X_RUNNER_EXTRA_SOURCE_DIR` no CMake do runtime, para
  compilar o C++ gerado direto de `work/` sem copiá-lo para o repositório.
- `env.toml`: `opt_level` e `unity_batch` para o build do código gerado.

### Corrigido
- PS2Recomp (`ExportPS2Functions.java`): funções cujo corpo o Ghidra divide em trechos separados eram
  exportadas com fim no endereço mais alto de qualquer trecho. No God Hand isso afetava 614 funções, que
  passavam a cobrir 1,57 MB em vez de 239 KB; uma função de 140 bytes virava 21,7 MB de C++ e o build não
  terminava. Agora a função acaba no trecho contíguo da entrada e os demais trechos viram entradas próprias.
  Mapa do God Hand regenerado: o C++ gerado caiu de 454 MB para 330 MB.
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
