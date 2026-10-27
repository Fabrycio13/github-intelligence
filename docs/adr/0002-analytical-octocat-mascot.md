# 0002. Redesenho do Mascote — Analytical Octocat

## Contexto
O mascote anterior baseado em wireframe poligonal mono-traço não transmitia calor nem identidade proprietária forte, gerando insatisfação visual. Em alinhamento interativo via `/grill-me`, definiu-se a substituição do ícone anterior por um mascote inspirado no universo do GitHub (Octocat), com traços originais, carismáticos e identidade própria voltada para inteligência, análise e evidências determinísticas.

## Decisão
Adotado o conceito do **Analytical Octocat**:
- **Conceito & Anatomia**: Híbrido felino + polvo (Octocat) com busto amigável, orelhas estilizadas e tentáculos equipados com terminações de fibra óptica luminosa e conectores de dados.
- **Assinatura Visual**: Monóculo HUD Holográfico projetado no olho direito, com anéis de retículo, retículo de mira e nódulos de commit do Git projetados em ciano neon (`#38bdf8`) e esmeralda (`#10b981`).
- **Paleta de Cores**: GitHub Dark Tech (`#111827`, `#1f2937`, `#334155`), com acentos de ciano neon (`#38bdf8`), verde esmeralda (`#10b981`) e branco puro para brilhos especulares.
- **Tipografia do Logotipo**: "GitHub" em off-white (`#f0f6fc`), "Intelligence" em ciano neon (`#38bdf8`) e subtítulo "DETERMINISTIC EVIDENCE" em cinza slate (`#8b949e`).
- **Ativos Produzidos**:
  - `src/github_intelligence/dashboard_assets/logo-icon.svg`: Ícone vetorial quadrado 1:1.
  - `src/github_intelligence/dashboard_assets/logo.svg`: Logotipo horizontal com tipografia.
  - `src/github_intelligence/dashboard_assets/logo-icon.png`: Raster 128x128 para favicon e app compact.
  - `src/github_intelligence/dashboard_assets/logo.png`: Raster horizontal 360x90 para a sidebar e cabeçalhos.
  - `docs/logo.png`: Cópia de alta resolução para documentação.

## Consequências
- A interface e documentação ganham identidade visual marcante, acolhedora e intimamente conectada ao universo GitHub sem ser cópia direta.
- Conformidade mantida com todos os testes automatizados do dashboard shell (`test_shell_serves_brand_logo_and_icon`).
- Regeneração e manutenção facilitada através do script `scripts/generate_logos.ps1`.
