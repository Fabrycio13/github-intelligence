# Persistência PostgreSQL restrita — piloto

## Estado

- Migration `0001` aplicada manualmente pelo usuário e catálogo conferido: ver `supabase-migration-0001-applied-2026-10-19.md`.
- O usuário informou a execução de `0002_runtime_login.sql`; consultas read-only confirmaram `github_intelligence_worker` e o vínculo restrito ao grupo runtime.
- `github-intelligence db check` passou contra o Supabase com o login worker, TLS `verify-full` e 10 tabelas conferidas (19/10/2026).
- Implementados conexão/diagnóstico e o fluxo local `db import`, que por padrão só mostra plano e exige `--apply` para coletar e gravar.
- Suíte local, Ruff e mypy devem ser tratados conforme os resultados de validação atuais; testes contra PostgreSQL local descartável não substituem o smoke remoto.
- **Nenhuma importação remota foi executada.** Não executar gravação remota sem revisão/aprovação explícita do escopo e dos dados. Não foi criada nem aplicada migration nova nesta implementação.

## Interface e isolamento

`uv run github-intelligence db check` verifica o login restrito, membership SET para runtime, ausência de privilégios perigosos, tabelas/ownership/RLS esperados, políticas de escopo e negação de leitura sem contexto. Não cria clientes/projetos, não define escopo de projeto, não sincroniza GitHub e não importa dados.

O login de runtime é `github_intelligence_worker`, separado do grupo NOLOGIN `github_intelligence_runtime`. A senha foi configurada pelo usuário por prompt local e não deve ser exposta em chat, comandos ou logs. Não usar `postgres`, `service_role` ou senha administrativa como credencial da aplicação.

Um worker continua sendo confiável para escolher contexto por transação. Esse mecanismo **não autentica usuários finais** e não substitui autorização de endpoints por membership.

## Importação explícita

```bash
uv run github-intelligence db import \
  --repo OWNER/REPO --client-id 00000000-0000-0000-0000-000000000001 \
  --project-id 00000000-0000-0000-0000-000000000002 \
  --since 2026-10-01T00:00:00Z --until 2026-10-19T00:00:00Z
```

Sem `--apply`, esse comando valida a seleção local e imprime o plano; não acessa GitHub nem PostgreSQL. Adicionar `--apply` é o caminho de gravação e exige autorização operacional específica: ainda não foi executado contra o Supabase. Cada execução aceita exatamente um repositório ativo. Antes de `--apply`, confira os UUIDs e o escopo no banco, a janela e o repositório.

O import exige que cliente, projeto, instalação GitHub e repositório já estejam provisionados no schema. O ID GitHub do repositório e os IDs do App/instalação local precisam corresponder às linhas ativas do banco. Se faltar qualquer vínculo, o preflight recusa a execução. A coleta completa e a renderização do relatório ocorrem antes da conexão ao banco; depois o comando roda `db check` e grava snapshot(s) allowlisted, relatório, fontes e auditoria em transações scoped. Falhas de persistência fazem rollback dos snapshots/relatório e tentam finalizar a execução como `failed`.

Snapshots são observações históricas imutáveis; repetir uma coleta em outro momento gera nova observação, não sobrescreve a anterior. Não são gravados corpos de PR, diffs ou código bruto. A trilha/auditoria do worker não representa autorização de usuário final.

## Configuração

Configuração não secreta opcional: `config/database.local.json`, ou caminho explícito antes do subcomando:

```bash
uv run github-intelligence --database-config CAMINHO_JSON db check
```

Campos permitidos no JSON: `host`, `user`, `project_ref`, `port`, `password_path`, `sslrootcert`. Não são aceitos DSN, password nem campos desconhecidos/duplicados. Banco `postgres` e porta `5432` são fixos nesta etapa. Um caminho explícito inválido falha; sem arquivo opcional, a configuração pode vir integralmente do ambiente.

Overrides de ambiente:

- `GHI_DATABASE_HOST`
- `GHI_DATABASE_USER`
- `GHI_DATABASE_PROJECT_REF`
- `GHI_DATABASE_PASSWORD` (somente via canal/ambiente seguro; não colar em chat ou histórico de comandos)
- `GHI_DATABASE_PASSWORD_PATH`
- `GHI_DATABASE_SSLROOTCERT`

Senhas em arquivo são aceitas somente como blob DPAPI `.dpapi`, com marcador válido e conteúdo UTF-8. Preferir arquivo fora do repositório, protegido pelo usuário Windows. O leitor DPAPI existente é reaproveitado como codec de bytes; **não utilizar a chave privada da GitHub App como senha do banco**. Caminhos relativos em JSON são resolvidos em relação ao próprio JSON.

## Transporte

- Conexão direta: hostname deve corresponder ao project ref configurado e username ao login restrito.
- Session pooler compartilhado: hostname AWS Supabase validado e username composto pelo login restrito e o mesmo project ref; somente porta 5432.
- Descobrir o endpoint real pelo painel Connect do projeto, não adivinhar região de pooler. A viabilidade IPv6 local e o endpoint exato ainda não foram confirmados.
- Driver força `sslmode=verify-full`, `sslrootcert=system` por padrão, ou caminho existente para CA explicitamente configurada. Não há fallback para TLS inseguro.
- Timeout de conexão de 10 segundos, sem prepared statements automáticos; conexão encerrada em todas as saídas normais/de erro do context manager. Erros não exibem DSN, senha ou diagnóstico bruto do driver.

## Evidência e limites dos testes

- Testes do diagnóstico abrem uma conexão autenticada com login não-superuser em PostgreSQL nativo local descartável, não apenas SET ROLE em sessão administrativa.
- Cobrem negação de admin/sessão administrativa mascarada, deriva de BYPASSRLS, RLS desabilitada/não forçada, política permissiva, DELETE concedido, contexto herdado e transação já aberta.
- O diagnóstico rejeita DELETE/TRUNCATE tanto no login original quanto no grupo runtime. Confere também herança efetiva de membership (NOINHERIT no login não é suficiente) e direitos diretos de administração de papéis no worker.
- Rejeita memberships diretas/indiretas de `anon`, `authenticated` e `service_role` em owner/runtime, mesmo sem herança; detecta grants de schema, tabela, coluna e função para esses papéis, inclusive quando o privilégio de schema está ausente.
- O teste positivo da CLI percorre configuração por ambiente → comando público → diagnóstico real local. Somente o conector de rede é substituído; isso não substitui o smoke remoto.
- O `db check` autenticado ao Supabase passou com TLS `verify-full` em 19/10/2026. Os testes do transporte verificam também os argumentos do driver e sanitização/fechamento; não executam a importação.

## Próximos gates

1. Provisionar, sob revisão específica, cliente/projeto, membership, instalação GitHub e repositório no escopo do piloto; a migration de schema não insere essas linhas de negócio.
2. Conferir os UUIDs e IDs reais contra a seleção local e `config/app.local.json`.
3. Revisar/autorizar o conjunto de dados e o intervalo do primeiro import. O default sem `--apply` é somente plano.
4. Só após aprovação explícita, executar `db import ... --apply` para um único repositório e ler de volta run, snapshots, relatório e auditoria. Nenhuma escrita remota foi feita pelo agente.

Referências oficiais verificadas: documentação Supabase “Connect to your database” e PostgreSQL 17 “Database Connection Control Functions”, opções sslmode/sslrootcert.
