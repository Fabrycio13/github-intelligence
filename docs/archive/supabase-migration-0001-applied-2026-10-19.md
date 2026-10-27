# Migration 0001 — aplicação manual e conferência remota

## Registro

- Projeto Supabase: `cpkgvpuivjzplerzlpxz`.
- Aplicação: informada pelo usuário em 19/10/2026, após autorização explícita. O agente não executou o DDL remoto.
- Conferência: catálogo remoto consultado somente por SELECT com `supabase_read_only_user`, em 19/10/2026, aproximadamente 15:20 -03:00.
- Arquivo local de referência: `migrations/0001_initial.sql`.
- SHA-256 do arquivo local: `1aee4d3d8bdd8b6f36fba5e472e7d39f4ca17de93c99a39babcaf3c0b6de0958`.
- Esse hash identifica o arquivo local, não comprova igualdade byte a byte com o texto executado no painel. Os objetos abaixo foram conferidos no catálogo.
- `supabase_migrations.schema_migrations` não existe neste projeto. A aplicação é registrada aqui como manual; ausência de ledger não significa migration pendente.

**A migration 0001 agora é imutável. Não reaplicar nem editar seu SQL para corrigir o banco. Mudanças futuras exigem migration incremental e aprovação específica.**

## Evidências verificadas

1. Schema `github_intelligence`, owner `ghi_owner`.
2. Dez tabelas: `audit_events`, `clients`, `github_installations`, `pr_snapshots`, `project_memberships`, `projects`, `report_sources`, `reports`, `repositories`, `sync_runs`.
3. Todas as tabelas com RLS habilitada e forçada e owner `ghi_owner`.
4. Dez políticas `worker_scope`, limitadas ao grupo `github_intelligence_runtime`, com USING/WITH CHECK de cliente/projeto; clients e installations usam o escopo de cliente previsto no SQL.
5. Grupos `ghi_owner` e `github_intelligence_runtime` sem LOGIN, SUPERUSER, BYPASSRLS, CREATEDB, CREATEROLE, REPLICATION ou INHERIT. Memberships consultadas não concedem esses grupos aos papéis de navegador nem owner ao runtime.
6. `anon`, `authenticated` e `service_role` sem USAGE/CREATE no schema, sem privilégios consultados nas tabelas e sem EXECUTE nas cinco funções.
7. Runtime com SELECT nas tabelas; INSERT somente em execuções, snapshots, relatórios, fontes e auditoria; UPDATE somente em `repositories(status,updated_at)` e `sync_runs(status,started_at,finished_at,error_code)`; sem CREATE no schema, DELETE ou TRUNCATE.
8. Cinco funções pertencem a `ghi_owner`, SECURITY INVOKER e search_path fixado em `pg_catalog`. Runtime pode executar diretamente somente as duas funções de contexto.
9. Função `require_running_snapshot` contém a correção `FOR SHARE`.
10. Seis triggers não internos habilitados: quatro de imutabilidade, um de transição de execução e um de exigência de execução running para snapshots.
11. Quatorze FKs validadas, com ON DELETE RESTRICT e as chaves compostas de cliente/projeto/repositório/execução previstas.
12. Confirmados os índices únicos de execução ativa por repositório, PR por execução e fonte de relatório com NULLS NOT DISTINCT.
13. Nenhuma configuração `pgrst.db_schemas` foi encontrada no catálogo consultado para este banco/configuração global. Isso não inspeciona configuração externa da Data API; a negação de privilégios de navegador foi conferida separadamente.

## Limites e próximo gate

- **GO: estrutura inicial aplicada e proteções acima conferidas no catálogo remoto.**
- Não foram executados INSERT/UPDATE/DELETE nem importações pelo agente durante esta conferência.
- O conteúdo das tabelas não foi auditado e não se afirma que estejam vazias.
- Os 400 testes aprovados, incluindo isolamento e concorrência, foram executados no PostgreSQL local descartável, não no Supabase.
- Não foi provisionado login/senha de aplicação pelo agente. O grupo runtime continua NOLOGIN; não é uma credencial de conexão.
- **NO-GO para afirmar integração operacional:** ainda falta autorizar/provisionar conexão restrita de runtime, conectar a aplicação e executar smoke ponta a ponta com dados do piloto. Não utilizar postgres/service_role como credencial cotidiana da aplicação.
- Login/membership de usuários finais, interface, agendamento, IA e e-mail continuam fora desta entrega de schema.
