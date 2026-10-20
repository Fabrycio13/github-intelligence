# Migration inicial — aplicada manualmente e catálogo conferido

`0001_initial.sql` foi aplicada manualmente pelo usuário ao projeto Supabase `cpkgvpuivjzplerzlpxz` em 19/10/2026. O agente confirmou a estrutura e as proteções no catálogo remoto, somente por consultas de leitura. **Não reaplicar nem editar esta migration: mudanças futuras exigem arquivo incremental.** Registro e limites em `docs/supabase-migration-0001-applied-2026-10-19.md` (caminho relativo à raiz do projeto).

O cabeçalho de `0001_initial.sql` ainda diz “REVIEW DRAFT”; é um rótulo histórico desatualizado e o arquivo permanece intocado por ser aplicado. Para o status remoto, use o registro acima e o catálogo conferido. O usuário informou a aplicação de `0002_runtime_login.sql`; o papel worker e a conexão restrita foram conferidos posteriormente. Não reaplicar `0001` ou `0002` para habilitar funcionalidades novas.

`0003_repository_onboarding.sql` é o rascunho incremental para cadastrar repositórios pela tela: concede INSERT nas colunas explícitas de instalações e repositórios e EXECUTE em um helper fixo de travamento ao runtime. O helper usa o owner NOLOGIN existente e três políticas de owner com o mesmo escopo RLS para bloquear alterações simultâneas de status durante o vínculo. Não concede UPDATE ou membership de owner ao worker, não cria clientes/projetos/usuários e não inicia coleta. Está validado em PostgreSQL local, mas **não aplicado remotamente**; exige revisão e autorização próprias. Detalhes em `docs/repository-onboarding.md`.

## Escopo

- Cria exclusivamente schema `github_intelligence`, tabelas, índices, funções/triggers e políticas da aplicação.
- Referencia `auth.users(id)` já existente; não cria nem modifica tabelas gerenciadas de auth/storage.
- Cria os grupos NOLOGIN `ghi_owner` e `github_intelligence_runtime`, se ainda não existirem, e rejeita flags/pertenças inseguras conhecidas em grupos existentes.
- Concede ao operador de migration a possibilidade de SET para `ghi_owner`, necessária para transferir ownership sem superuser. Não concede isso ao runtime nem aos papéis de navegador.
- Runtime não tem login/senha provisionados neste bloco. Um login de aplicação restrito e sua credencial precisam ser autorizados separadamente, sem senha no chat/SQL versionado.
- `0002_runtime_login.sql` prepara somente o papel LOGIN restrito e sua membership, sem senha; o provisionamento de senha é uma etapa separada e segura.
- Exige papéis Supabase `anon`, `authenticated`, `service_role` e a tabela `auth.users`. O teste local cria apenas substitutos sintéticos desses pré-requisitos no banco descartável.
- Sem seed, importação da POC, grants ao navegador, conexão automática da CLI ou envio de dados para o banco remoto.

## Proteções

- Schema privado, sem USAGE/grants para PUBLIC, anon, authenticated e service_role. Guards de exposição PostgREST em configurações de banco conhecidas; verificar também configuração externa antes de aplicação real.
- RLS habilitada e forçada em todas as tabelas da aplicação; runtime sem superuser/BYPASSRLS e sem ownership.
- Contexto de cliente/projeto por transação para workers confiáveis. **Isso não autentica usuários**: um worker que pode configurar contexto é confiável, e os futuros endpoints precisam validar login/membership antes de escolhê-lo.
- FKs compostas bloqueiam referências cruzadas mesmo sob o operador privilegiado.
- Registro de estado da execução; índice único impede duas coletas pending/running para o mesmo vínculo.
- A inserção de snapshot mantém lock SHARE sobre a execução até o commit, impedindo finalização concorrente enquanto esse snapshot ainda está pendente.
- Snapshots, relatórios, fontes e auditoria imutáveis. Sem DELETE concedido ao runtime.
- Payloads devem passar pela whitelist da aplicação. Títulos/metadados ainda podem conter informação sensível; não há alegação de DLP universal.

## Aplicação e recuperação

Aplicar uma vez, em sessão autorizada de migration e com parada em erro. `BEGIN/COMMIT` fazem o bloco ser atômico; uma falha exige rollback da sessão. Repetir em schema existente deve falhar, não mascarar divergência com CREATE TABLE IF NOT EXISTS.

Não há down destrutivo. Depois de aprovada e aplicada, correções entram em migration nova, com análise de dados e aprovação; nunca apagar o schema/histórico para tentar novamente.

## Testes locais

Preparar um PostgreSQL **nativo real**, isolado e sem serviço instalado. O harness usa somente loopback, banco `ghi_test_*` e diretório temporário de teste. Nunca usa DATABASE_URL/Supabase por fallback.

```bash
uv sync --locked
uv run python scripts/prepare_test_postgres.py
uv run pytest -q -ra
uv run mypy
uv run ruff check src tests scripts
```

`GHI_PG_BIN` pode selecionar o diretório dos executáveis de teste. Sem binários, integração precisa ser explicitamente marcada não executada; não substituir por SQLite, mocks ou banco remoto.

### Evidências locais — 19/10/2026

- PostgreSQL nativo 17.11, somente `127.0.0.1`, bancos descartáveis `ghi_test_*`.
- Suite completa: **400 testes aprovados**, incluindo integração real, isolamento, imutabilidade e concorrência; mypy em 13 arquivos e Ruff aprovados.
- Regressão reproduziu a finalização indevida durante INSERT não confirmado. Após adicionar `FOR SHARE`, a finalização aguarda o snapshot; o caso inverso também é testado.
- A transferência de ownership foi validada usando conexão autenticada com um operador local **não-superuser**, não apenas SET ROLE numa sessão de superuser. Membership SET e CREATE no schema já estão explícitos no SQL. O teste confirma o owner final e ausência de CREATE no banco para `ghi_owner`; não foi necessário ampliar esse grant.
- Esses testes locais **não substituem** a conferência remota nem o smoke da aplicação. A aplicação manual e a leitura posterior do catálogo estão registradas no documento acima; conexão de runtime e importação de dados continuam pendentes.

## Gate para o Supabase

1. Revisar SQL integral e resultados reais dos testes.
2. Reconsultar catálogo do projeto exato e confirmar que não houve divergência.
3. Confirmar conexão/papel de migration e permissões; o conector de auditoria usado até aqui é somente leitura.
4. Obter aprovação explícita para aplicar este SQL, separada da autorização para prepará-lo.
5. Após aplicação autorizada, ler de volta tabelas, constraints, grants, ownership e RLS antes de importar qualquer dado.
