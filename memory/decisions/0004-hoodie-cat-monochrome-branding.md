# 0004. Redesenho de Identidade — Hoodie Cat Monocromático & Tipografia de Alto Contraste

## Contexto
O mascote anterior (Analytical Octocat com monóculo HUD ciano) apresentava complexidade excessiva, elementos neon que causavam desconforto visual e ilegibilidade severa da tipografia do produto quando escalada na barra lateral. O usuário solicitou explicitamente:
1. Um mascote com traços mais caricatos, desenhados e cartunescos — um gatinho de capuz em espaço negativo (estilo dev hacker).
2. Proibição estrita de neon ("não quero nada neon") e de elementos genéricos de HUD futurista.
3. Posicionamento rigoroso: mascote à esquerda `<<` e nome do produto à direita `>>>`.
4. Tipografia de alto contraste e grande legibilidade, eliminando microlegendas ilegíveis.
5. Originalidade formal para evitar violações de direitos autorais/copyright.

## Decisão
1. **Mascote Hoodie Cat (Espaço Negativo)**:
   - Silhueta branca/off-white (`#f0f6fc`) fluida representando um capuz de desenvolvedor com orelhas pontiagudas de gato emergindo naturalmente da curvatura.
   - Cavidade interna do capuz em preto profundo (`#080c14`).
   - Focinho e bochechas felinas delineados em espaço negativo claro, com nariz triangular escuro e três pares de bigodes dinâmicos e expressivos.
   - Cordões do capuz estilizados com agulhas metálicas discretas em slate (`#cbd5e1`).
2. **Tipografia & Legibilidade**:
   - Disposição horizontal pura com proporção 4:1 (360x90).
   - "GitHub" renderizado em branco puro (`#ffffff`), peso 800, 28px/26px.
   - "Intelligence" renderizado em slate suave (`#cbd5e1`), peso 700, 22px/21px.
   - Remoção de subtítulos minúsculos que degradavam em telas padrão.
3. **Harmonia na Sidebar**:
   - Ajuste de dimensões de `.brand-image` para 168x42px, garantindo nitidez sem distorção.
   - Reordenação flexbox: Logotipo alinhado à esquerda (`order: 1`), e botão de recolher menu posicionado no canto superior direito (`order: 2`), permitindo ao nome respirar sem compressão lateral.
   - Remoção de brilhos e glows cianos nos estados ativos do menu e avatar do widget.
4. **Ativos Gerados**:
   - `scripts/generate_logos.ps1`: Script determinístico de renderização GDI+.
   - `src/github_intelligence/dashboard_assets/logo-icon.png` (128x128).
   - `src/github_intelligence/dashboard_assets/logo.png` (360x90).
   - `docs/logo.png` (360x90).
   - `src/github_intelligence/dashboard_assets/logo-icon.svg` (SVG vetorial 1:1).
   - `src/github_intelligence/dashboard_assets/logo.svg` (SVG vetorial horizontal 4:1).

## Consequências
- A marca adquire visual moderno, minimalista e autoral com zero poluição luminosa ou neon.
- Nome "GitHub Intelligence" torna-se 100% legível e instantaneamente reconhecível na barra lateral.
- Conformidade total com a suíte de testes de regressão do dashboard shell (`test_dashboard_shell.py`, `test_dashboard_data.py`, etc.).
