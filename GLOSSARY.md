# GitHub Intelligence — Glossário de Domínio

Vocabulário canônico do GitHub Intelligence: coleta somente leitura de repositórios, persistência em PostgreSQL/Supabase e painel operacional com evidências determinísticas.

## Linguagem

### Interface & Apresentação

**Painel Local**:
Interface web servida estritamente em loopback (127.0.0.1:8765) para o operador local inspecionar repositórios, autorizar conexões, disparar coletas sob demanda e consultar relatórios de evidências.
_Avoid_: Web app, SaaS multiusuário, plataforma em nuvem.

**Git Cat**:
Mascote autoral em vetor monoline geométrico que combina a silhueta felina com a topologia de branches e nós de commit do Git.
_Avoid_: Octocat, mascote genérico, cervo neon, ilustração cartoon.

**Paleta Anti-Fadiga**:
Sistema de cores estruturado em tons de ardósia aveludada (`#0d1117` e `#161b22`) com texto primário em off-white amaciado (`#e6edf3`), projetado para ergonomia de leitura técnica prolongada sem cansaço visual.
_Avoid_: Vidro fosco, frosted glass, preto puro (#000000), neon, gradientes chamativos.

**Modo Claro Adaptativo**:
Variação suave de alto contraste e baixo brilho para ambientes claros (`#f6f8fa` e `#ffffff` com bordas `#d0d7de`), ativada pelo sistema operacional sem emissão de luz ofuscante.
_Avoid_: Tema branco estourado, light mode padrão com 100% de luminosidade.

**Semáforo Semântico**:
Convenção visual estrita que traduz o estado determinístico de coletas e conexões em quatro cores funcionais: verde esmeralda (sucesso/OK), âmbar (parcial/atenção), vermelho rubi (falha) e azul técnico (navegação).
_Avoid_: Badges decorativas, tags coloridas aleatórias.

**Loader de Precisão**:
Indicador de carregamento enxuto composto por micro-pulso do mascote e barra de progresso linear fina no topo, sincronizada exclusivamente às etapas reais de consulta às APIs.
_Avoid_: Splash teatral, animação SVG morphing pesada, spinner infinito falso.

**Contenção de Leitura**:
Tratamento estruturado para a exibição de relatórios brutos (`<pre>`), limitando a linha a uma extensão de leitura confortável com entrelinhamento e tipografia monoespaçada legível.
_Avoid_: Bloco cru sem contenção, texto expandido para toda a largura ultrawide.

### Operação & Domínio

**Relatório de Evidências**:
Documento Markdown determinístico gerado a partir de observações de PRs, revisões e status em um intervalo UTC explícito, contendo fontes rastreáveis.
_Avoid_: Auditoria de IA, code review automatizado, relatório de produtividade.

**Coleta**:
Operação manual e confirmada de leitura da API do GitHub para um repositório vinculado em uma janela temporal fechada.
_Avoid_: Sincronização em background, scheduler, webhook automático.

**Vínculo de Repositório**:
Associação persistida entre uma identidade confirmada no GitHub e um par cliente/projeto cadastrado, validada previamente pela GitHub App.
_Avoid_: Conexão anônima, integração silenciosa.
