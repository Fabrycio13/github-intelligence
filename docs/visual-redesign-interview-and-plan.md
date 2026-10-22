# Redesenho Visual do GitHub Intelligence — Registro da Entrevista e Plano de Implementação

Documento de registro do processo de alinhamento com a técnica **/grill-me**, detalhando a árvore de decisões, os pontos de inflexão, as escolhas de design e o plano passo a passo de engenharia para a implementação visual.

---

## 1. Registro da Entrevista (Design Tree Rounds)

### Rodada 1 — Identificação das Dores Centrais e Direcionamento
- **Q1 (Estilo Geral)**: Proposta de migração para o padrão *Modern Developer Tool* (Linear / Vercel / GitHub Dark).
  - *Decisão do Usuário*: Adotar visual minimalista profissional, evitando expressamente qualquer aparência de "template genérico gerado por IA".
- **Q2 (Plano de Fundo e Vidro Fosco)**: Proposta de remoção do wallpaper fotográfico (`bg.webp`) e do efeito de vidro fosco translúcido.
  - *Decisão do Usuário*: Sim, eliminar o papel de parede; foco em modernidade com superfícies que não cansem a vista do operador durante a leitura contínua.
- **Q3 (Identidade da Marca e Logotipo)**: Proposta de remoção da ilustração de cervo neon.
  - *Decisão do Usuário*: Sim, substituir por algo de nível profissional no universo Git — especificamente um mascote felino com estética própria e exclusiva.
- **Q4 (Carregamento e Peso de Código)**: Proposta de remoção do splash screen do Uiverse com 5.000+ linhas de CSS.
  - *Decisão do Usuário*: Sim, simplificar o loader e enxugar o CSS mantendo código limpo, alta performance e elegância estética sem perder a beleza.
- **Q5 (Cores de Status)**: Proposta de instituição de um semáforo de dados padronizado.
  - *Decisão do Usuário*: Sim, instituir semáforo de dados padronizado.

### Rodada 2 — Refinamento Estético e Conforto Visual
- **Q1 (Paleta Anti-Fadiga)**: Adoção de tons de ardósia aveludada (`#0d1117` e `#161b22`) com off-white amaciado (`#e6edf3`) e micro-bordas discretas (`#30363d`), eliminando o contraste estridente de preto absoluto (#000) e branco puro (#fff).
  - *Decisão do Usuário*: Aprovado integralmente.
- **Q2 (Design do Mascote Git Cat)**: Construção do mascote em vetor monoline geométrico que integra traços felinos com branches e nós de commit do Git.
  - *Decisão do Usuário*: Aprovado integralmente.
- **Q3 (Mecânica do Loader)**: Splash leve de carregamento com micro-pulso da silhueta do mascote e barra de progresso linear fina no topo sincronizada aos estágios reais de consulta à API.
  - *Decisão do Usuário*: Aprovado integralmente.
- **Q4 (Ergonomia dos Relatórios)**: Estruturação dos relatórios salvos com contenção de largura de leitura, espaçamento vertical respirável e tipografia monoespaçada legível.
  - *Decisão do Usuário*: Aprovado integralmente.

### Rodada 3 — Fechamento da Fronteira e Entregáveis
- **Q1 (Suporte a Modo Claro)**: Manter adaptação suave ao Modo Claro com tons de papel amaciado (`#f6f8fa`/`#ffffff`) via `prefers-color-scheme: light` sem ofuscamento de visão.
  - *Decisão do Usuário*: Aprovado integralmente.
- **Q2 (Geração de Assets)**: Geração dos arquivos de marca em SVG vetorial de alta definição e em PNG (`logo.png` e `logo-icon.png`), mantendo compatibilidade total com os testes automatizados e o favicon.
  - *Decisão do Usuário*: Aprovado integralmente.

---

## 2. Mapa do Design System

```
                  ┌─────────────────────────────────────┐
                  │      GitHub Intelligence UI         │
                  └──────────────────┬──────────────────┘
                                     │
      ┌──────────────────────────────┼──────────────────────────────┐
      │                              │                              │
┌─────┴───────────────┐  ┌───────────┴─────────────┐  ┌─────────────┴─────────────┐
│  Paleta Anti-Fadiga │  │   Mascote "Git Cat"     │  │   Semáforo & Ergonomia    │
├─────────────────────┤  ├─────────────────────────┤  ├───────────────────────────┤
│ Base:    #0d1117    │  │ Vetor monoline geom.    │  │ Sucesso:  #10b981 (Esmer.)│
│ Cartão:  #161b22    │  │ Nós de commit integrados│  │ Atenção:  #f59e0b (Âmbar) │
│ Borda:   #30363d    │  │ Assets SVG e PNG        │  │ Erro:     #ef4444 (Rubi)  │
│ Texto:   #e6edf3    │  │ Favicon 16x16 nítido    │  │ Relatório: contenção 88ch │
│ Muted:   #8b949e    │  │ Sidebar expandida/recolh│  │ Tipografia: JetBrains/SF   │
└─────────────────────┘  └─────────────────────────┘  └───────────────────────────┘
```

---

## 3. Plano Passo a Passo de Implementação

### Fase 1: Geração dos Novos Assets de Marca (Git Cat)
1. **Criação do SVG Autoral do Git Cat**:
   - Vetor monoline com silhueta felina estilizada e elementos de branches/nós Git conectados.
   - Versão compacta/ícone: `src/github_intelligence/dashboard_assets/logo-icon.svg`.
   - Versão completa com tipografia: `src/github_intelligence/dashboard_assets/logo.svg`.
2. **Geração dos PNGs de Alta Resolução**:
   - `src/github_intelligence/dashboard_assets/logo-icon.png` (formato quadrado para favicon e sidebar recolhida, tamanho > 1KB para atender aos testes).
   - `src/github_intelligence/dashboard_assets/logo.png` (formato horizontal para a sidebar expandida, tamanho > 1KB).
3. **Validação de Contrato**:
   - Executar `tests/test_dashboard_shell.py` para garantir que `test_shell_serves_brand_logo_and_icon` responde HTTP 200 com tamanho válido.

### Fase 2: Limpeza Estrutural e Remoção de Bloat
1. **Limpeza do `index.html`**:
   - Remover as centenas de tags `<path>` do morphing geométrico do Uiverse em `#svg-container`.
   - Inserir o novo mascote Git Cat e a barra de progresso linear no topo `#staged-progress-bar`.
   - Preservar rigorosamente IDs de navegação e componentes (`#initial-loading`, `#app-shell`, `#sidebar-toggle`, etc.).
2. **Enxugamento do `app.css`**:
   - Remover mais de 5.000 linhas de keyframes mortos (`@keyframes move7050`, etc.).
   - Remover regras de plano de fundo associadas a `url("/bg.webp")` e desfoques artificiais pesados.
   - Reduzir o CSS de ~70KB para ~18KB mantendo cobertura total de estilos.

### Fase 3: Aplicação da Paleta Anti-Fadiga e Estilos
1. **Dark Theme Base**:
   - Definir variáveis `:root` com paleta aveludada (`#0d1117`, `#161b22`, `#1c2128`, `#30363d`, `#e6edf3`, `#8b949e`).
   - Aplicar micro-bordas elegantes `1px solid var(--border-subtle)` e cantos arredondados sutis (`6px` / `8px`).
2. **Modo Claro Adaptativo Suave**:
   - Configurar `@media (prefers-color-scheme: light)` com fundo em `#f6f8fa`, cartões em `#ffffff` e bordas `#d0d7de`, sem branco ofuscante.
3. **Semáforo Semântico de Engenharia**:
   - Padronizar badges, status e gráficos com verde esmeralda (`#10b981`), âmbar (`#f59e0b`), vermelho rubi (`#ef4444`) e azul técnico (`#38bdf8`).
4. **Contenção e Ergonomia de Leitura dos Relatórios**:
   - Formatar `#report-content` com largura máxima de leitura confortável (~88ch), fundo de contraste acolhedor, espaçamento de linha `1.6` e destaque limpo para cabeçalhos e tabelas.

### Fase 4: Loader de Precisão com Micro-Pulso
1. **CSS do Loader (<40 linhas)**:
   - Posicionamento central do Git Cat com pulso suave de opacidade (`0.7` a `1.0`).
   - Barra de progresso linear de 2px no topo (`#staged-progress-bar`) conectada aos eventos do [app.js](file:///c:/Users/Usabit/Desktop/Projetos/GitHub%20Intelligence/src/github_intelligence/dashboard_assets/app.js).
   - Efeito de *fade-out* ao término de carregamento (`opacity 0.25s ease-out`).

### Fase 5: Verificação e Bateria de Testes
1. `node --test tests/sidebar_toggle.test.cjs` — Validação de navegação e recolhimento da sidebar.
2. `uv run pytest -q tests/test_dashboard_shell.py` — Validação dos contratos HTML, imagens e ARIA.
3. `uv run pytest -q tests/test_dashboard*.py` — Validação de toda a suíte do dashboard.
4. `uv run ruff check src tests` e `uv run mypy` — Checagem estática de integridade.
