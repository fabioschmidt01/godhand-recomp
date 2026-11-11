# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), com
[versionamento semântico](https://semver.org/lang/pt-BR/). Cada versão recebe uma tag `vX.Y.Z`.

Enquanto o jogo não roda, as versões são `0.x`: `0.MINOR` muda a cada fase concluída do roadmap,
`PATCH` para correções.

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
