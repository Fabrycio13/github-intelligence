# Validação da POC — 19/10/2026

## Resultado executado

- Ambiente instalado com `uv sync`; dependências travadas em `uv.lock`.
- `uv run pytest -q --basetemp C:/Users/Fabrycio/AppData/Local/hermes/cache/scratch/github-intelligence-pytest`: **242 passed**.
- `uv run mypy`: **Success: no issues found in 10 source files**.
- `uv run ruff check src tests`: **All checks passed**.
- CLI real, acesso público explícito, alvo autorizado pelo usuário: `Fabrycio13/project-mds`.
- Execução real com intervalo `[2026-10-19T00:00:00Z, 2026-10-19T11:41:29.028248Z)` UTC: exit **0**, documento `reports/project-mds-pr1-validacao.md` lido após geração.

## Evidências reais observadas

- Repositório GitHub ID `1226750215`, público.
- PR **#1**, aberto, origem `feat/memory-a11y-seo-tests`, destino `main`.
- SHA observado `dc597708861819dcb79a70bbce60a3ebe930c4df`.
- Um commit no PR; nenhuma review retornada; nenhuma aprovação observada.
- Check `Vercel Preview Comments`: completed/success.
- Commit status `Vercel`: success mais recente, antecedido por pending.
- API retornou `merged=false`, `mergeable=true`, `mergeable_state=clean`.
- Relatório corretamente manteve elegibilidade desconhecida por regras/permissões não verificadas. Deploy e validação funcional desconhecidos, sem inferência a partir do status Vercel.
- Links de PR, reviews, commits, checks e statuses presentes no documento.

A aprovação do cenário CI foi comparada diretamente a uma segunda consulta real dos endpoints. Os testes com reviews/aprovações e falhas são simulações explicitamente identificadas como testes, não atividades reais do repositório.

## Correções na integração

- Separadas notas normais de cobertura e falhas reais de coleta: cobertura limitada não apaga observações válidas.
- Commit statuses vinculados ao SHA do endpoint consultado, pois objetos individuais não precisam trazer campo sha.
- Relatório parcial identificado dentro do documento, não só no terminal.
- Resposta inválida de um repositório não impede relatório parcial dos outros.
- CI com falha ou solicitação de mudança sem política/permissões comprovadas não viram bloqueio confirmado de merge.
- Mergeabilidade nula em PR fechado não é falha de coleta; cálculo nulo inicial pode ser resolvido na reconsulta limitada.

## Reconsulta após revisão independente

- Revisão Standards: dois achados; revisão Spec: quatro achados, com sobreposição. Todos os achados foram corrigidos e cobertos por regressões.
- Não se afirma bloqueio por CI opcional ou review de autoridade desconhecida; mergeabilidade nula de PR terminal não invalida coleta.
- Destino histórico de merge permanece desconhecido sem fonte histórica, mesmo que a base atual corresponda à homologação.
- Atores/apps malformados são rejeitados na coleta e produzem parcial; transições de draft são detectadas e preservam o último flag observado.
- Gates finais repetidos: **242 testes passando**, mypy sem erros em dez arquivos e Ruff aprovado.
- Nova execução real exit **0**, intervalo `[2026-10-19T00:00:00Z, 2026-10-19T11:46:46.361375Z)`, documento `reports/project-mds-pr1-revisado.md` lido após geração.
- Essa execução detectou o segundo commit enviado pelo usuário: novo head `5fd6686638cdf2b1fa0f6e4390e7c1519668a764`, dois commits no PR, nenhuma aprovação observada e CI observado passando. O registro anterior permanece válido como snapshot de sua própria execução.

## Smoke real após merge

- API do PR #1 retornou `state=closed`, `merged=true`, `merged_at=2026-10-19T11:53:42Z` e `merged_by.login=Fabrycio13`.
- Base atual retornada: `main`; isso não é usado para inventar confirmação histórica de homologação.
- Nova execução da CLI pública retornou exit **0** e gerou `reports/project-mds-pr1-pos-merge.md`, lido após geração.
- Relatório detectou o evento de merge e o ciclo `merged`, preservou as duas observações de commits e registrou o responsável pelo merge. Deploy e validação funcional permaneceram `unknown`.
- Nenhuma escrita foi realizada no GitHub pelo coletor. Merge foi executado pelo usuário.

## Gate autenticado e privado — concluído

App `5380709`, instalação `173020772`, validada com seleção restrita aos repositórios autorizados e todas as permissões em leitura. `Fabrycio13/skills` confirmou `private=true`. A configuração persistida contém apenas IDs e referência à chave, que foi criptografada com DPAPI fora do projeto e teve a descriptografia conferida antes da remoção do PEM gerado.

CLI real: `repos available`, `repos add Fabrycio13/skills`, `repos list` e `report` executados com sucesso, sem credenciais nos argumentos. O relatório `reports/pilot-app-dpapi.md` foi lido: dois repositórios autenticados, quatro PRs na amostra, sem parcial. Intervalo `[2026-10-01T00:00:00Z, 2026-10-19T12:56:31.632885Z)` UTC.

Gates finais: **275 testes passando**, mypy aprovado em 11 arquivos e Ruff aprovado. Detalhes de operação, restrição inicial da App à conta pessoal e limitações do DPAPI em `docs/github-app-setup.md`.

## Limites e gates

**GO:** POC de leitura pública e autenticada/privada, coleta dos PRs reais e geração Markdown com chave criptografada no piloto local.

**NÃO VALIDADO:** reviews/aprovações reais, regras completas de elegibilidade, múltiplas instalações de clientes, segurança multi-tenant de produção e histórico transacional.

Ainda sem front-end, FastAPI, banco, agendamento, IA, e-mail ou coleta de deployments. O ambiente usa referências locais para a App; não exige segredos em variáveis de ambiente ou no comando. Nenhuma credencial foi solicitada por chat.

Nenhum Git init, branch, commit ou push foi realizado neste projeto. Nenhum comentário, aprovação ou merge foi realizado no repositório piloto. Revisões independentes de engenharia e spec concluídas; achados corrigidos com testes de regressão e gates finais repetidos.
