# 0003. Redesenho do Dashboard — Workstation em Ilha Flutuante (Canvas Base + Elevated Sheet)

## Contexto
O layout anterior apresentava um aspecto de dashboard genérico de IA com cards soltos e pouca hierarquia de planos. A partir de alinhamento com imagem de referência via `/grill-me`, definiu-se uma reestruturação profunda baseada no conceito visual **Canvas Base + Floating Island** (inspirado em interfaces modernas de alta densidade como Linear, Raycast e o modelo de folha elevada da referência):
- Uma **base escura sólida unificada** (`#080c14`) cobrindo toda a janela;
- A **sidebar integrada à base** contendo a navegação e um **Widget Operacional com o mascote Analytical Octocat** no rodapé;
- O **Dashboard como uma Folha/Ilha Flutuante independente** à direita (`#111722`), com margens, 4 cantos arredondados (`border-radius: 24px`), borda hairline e sombra de elevação profunda.

## Decisão e Implementação
1. **Camada 0 — Base Canvas & Sidebar Integrada**:
   - Fundo contínuo `#080c14` sob todo o viewport (`body` e `#app-shell`).
   - Sidebar com fundo transparente integrado à base e divisor sutil (`rgba(255, 255, 255, 0.05)`).
   - Links de navegação com bordas arredondadas e estado ativo em ciano neon com brilho difuso (`rgba(56, 189, 248, 0.12)`).
   - **Widget Operacional do Analytical Octocat**: card estilizado no rodapé da sidebar (inspirado no card "Go PRO" da referência) com avatar do mascote, selo pulsante `App Ativa`, ID da GitHub App (`5380709`) e selo `v1.0 Nativo · DPAPI + RLS`.
2. **Camada 1 — Folha do Dashboard Elevada (`#main-content`)**:
   - Margens de ilha flutuante (`margin: 14px 16px 14px 264px; width: calc(100% - 280px)`).
   - Superfície em Dark Slate (`#111722`) com gradiente sutil superior (`--surface-highlight`), bordas hairline (`rgba(255, 255, 255, 0.08)`) e cantos expressivos (`border-radius: 24px`).
   - Sombra de elevação multicamada: `box-shadow: 0 16px 44px rgba(0, 0, 0, 0.65), 0 2px 6px rgba(0, 0, 0, 0.4)`.
3. **Header Integrado da Folha**:
   - Lado esquerdo: Pill de status de conexão (`● GitHub App Conectada`) acima do título da página.
   - Lado direito: Perfil do operador (`Fabrycio13 · Dev Intelligence`) com avatar e botões de ação rápida (`Adicionar repositório` e `Recarregar dados`).
4. **Feed de Evidências Recentes com Busca Instantânea e Drawer Lateral**:
   - Campo de pesquisa em tempo real (`#feed-search-input`) para filtrar repositórios e datas sem recarregar.
   - Slide-over drawer lateral (`#report-drawer`) para inspecionar relatórios com botão de cópia de Markdown e atalho `Esc`, preservando a visão do dashboard.
5. **Cache-Busting & Testes**:
   - Versão dos assets atualizada para `v=12` em [index.html](file:///c:/Users/Usabit/Desktop/Projetos/GitHub%20Intelligence/src/github_intelligence/dashboard_assets/index.html).
   - Todos os 35 testes automatizados do dashboard validados e aprovados.

## Consequências
- A interface ganha presença visual de produto profissional de alta sofisticação, separando claramente a infraestrutura de navegação (base) da área de trabalho ativa (ilha elevada).
- Nenhum contrato de rotas, segurança ou API foi violado.
