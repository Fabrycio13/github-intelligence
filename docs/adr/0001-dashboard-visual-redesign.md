# 0001. Redesenho Visual do Painel Local (Anti-Fadiga e Clean Engineering)

Reestruturação completa da apresentação visual do painel local, substituindo o papel de parede fotográfico e o logotipo de cervo neon por uma identidade técnica baseada em Dark Slate aveludado, mascote autoral "Git Cat" em vetor monoline geométrico, semáforo semântico estrito e remoção de 5.000 linhas de CSS infladas do loader.

## Contexto e Decisão
O painel do GitHub Intelligence utilizava transparência translúcida (*frosted glass*) sobre um papel de parede fotográfico de montanha (`bg.webp`), uma cabeça de cervo neon e um loader inicial com centenas de elementos SVG derivados do Uiverse, inflando o [app.css](file:///c:/Users/Usabit/Desktop/Projetos/GitHub%20Intelligence/src/github_intelligence/dashboard_assets/app.css) para mais de 70KB. Esse formato causava fadiga visual ao ler tabelas e relatórios técnicos longos, transmitia uma impressão de template casual/gamer e conflitava com o propósito determinístico e sóbrio da ferramenta.

Decidiu-se:
1. Eliminar o papel de parede fotográfico e a transparência difusa, adotando superfícies opacas sólidas e confortáveis (`#0d1117` e `#161b22`) com bordas precisas (`#30363d`), acompanhadas de um Modo Claro adaptativo suave (`#f6f8fa`/`#ffffff`).
2. Substituir o cervo neon pelo mascote autoral "Git Cat" em traço monoline vetorial geométrico, simbolizando nós de commit e branches de Git, fornecido tanto em SVG quanto em PNG (`logo.png` e `logo-icon.png`).
3. Substituir o splash teatral por um loader de precisão ultra-enxuto (<40 linhas de CSS) com micro-pulso da marca e barra linear no topo conectada às etapas reais da API.
4. Padronizar as cores de status em semáforo semântico restrito (esmeralda, âmbar e vermelho rubi).
5. Aplicar contenção de leitura e tipografia monoespaçada confortável nos relatórios salvos.

## Opções Consideradas
- **Manter Vidro Fosco / Wallpaper fotográfico**: Rejeitado porque a variação de brilho do fundo tornava a leitura de código e tabelas cansativa e inconsistente entre monitores.
- **Preto Puro (#000000) com Branco Puro (#FFFFFF) e Neons**: Rejeitado por cansar a vista (alto ofuscamento) e soar como interface amadora gerada por IA sem refinamento humano.
- **Paleta Ardósia Aveludada (#0d1117 / #161b22) com Off-White (#e6edf3)**: Escolhida por oferecer equilíbrio ideal entre contraste técnico e conforto prolongado, seguindo padrões de excelência de ferramentas como Linear, Raycast e GitHub Dark Pro.

## Consequências
- [app.css](file:///c:/Users/Usabit/Desktop/Projetos/GitHub%20Intelligence/src/github_intelligence/dashboard_assets/app.css) é reduzido em ~5.000 linhas de keyframes mortos, tornando a renderização instantânea e a manutenção limpa.
- O carregamento da página deixa de requisitar imagens pesadas de plano de fundo.
- A experiência de leitura e inspeção de relatórios técnicos fica organizada e confortável.
- Todos os contratos de acessibilidade, navegação por sidebar e testes de shell em [tests/test_dashboard_shell.py](file:///c:/Users/Usabit/Desktop/Projetos/GitHub%20Intelligence/tests/test_dashboard_shell.py) continuam integralmente atendidos.
