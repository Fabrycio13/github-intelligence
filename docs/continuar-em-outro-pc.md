# Retomada no PC do trabalho — 20/10/2026

Este é o ponto de retomada atual. Os documentos anteriores registram etapas históricas; afirmações antigas como “nenhuma importação remota” ou “0003 ainda não aplicada” não representam mais o estado atual.

## Estado confirmado antes da publicação

- CLI e painel local funcionais; coleta GitHub App somente leitura, persistência e relatórios no Supabase.
- Piloto: `personal / pilot`, repositório `Fabrycio13/project-mds`; coleta pelo painel já confirmada pelo operador.
- Cadastro por link implementado: inspeção de acesso, autorização externa quando necessária, nova verificação da identidade antes de confirmar. Vincular não inicia coleta.
- Migrations 0001/0002 já aplicadas; em 20/10 o usuário informou aplicar 0003. Conferência read-only posterior confirmou helper e grants de onboarding; `db check` passou com TLS verify-full e dez tabelas. **Não reaplicar migrations ou seed no PC novo.** O banco é o mesmo remoto.
- Última suíte completa: **768 passed**, Ruff aprovado e mypy aprovado em 22 arquivos. PostgreSQL real local descartável, sem Docker ou serviço Windows.
- Nenhum novo vínculo foi feito como smoke remoto após 0003. Próximo gate funcional: verificar pelo painel um repositório autorizado, confirmar explicitamente seu vínculo e ler de volta o cadastro; coletar somente com outra confirmação.
- Sem autenticação de usuário final, hospedagem, agendador, IA ou e-mail. Painel restrito ao loopback e a operador confiável; não expor na rede.

## 1. Obter código e desenvolver sem credenciais

Requisitos: Git, GitHub CLI autenticado com acesso ao repositório privado, Python >=3.11 e uv. Em Git Bash:

```bash
gh repo clone Fabrycio13/github-intelligence
cd github-intelligence
uv sync --locked
uv run github-intelligence --help
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy
```

Os testes de banco usam PostgreSQL nativo descartável no Windows; o harness prepara os binários locais na primeira execução. Não conectam ao Supabase. Consulte `tests/conftest.py` e `scripts/prepare_test_postgres.py` caso a rede corporativa bloqueie a preparação. Não substituir por SQLite/mocks e não instalar serviço ou iniciar Docker para contornar isso.

## 2. O que NÃO acompanha o clone

`config/*.local.json`, `config/repositories.json`, relatórios locais, chaves, senhas e blobs DPAPI estão ignorados. O histórico remoto de coletas continua no Supabase.

**DPAPI usa a proteção do usuário Windows atual. Copiar os blobs deste PC não configura o outro PC.** Não colocar credenciais no Git, no chat, em comandos visíveis ou em connection strings. Não usar conta administrativa como runtime.

A configuração do painel e os IDs da App não são segredos; há exemplos versionados. A senha do worker e a chave privada da App exigem preparação segura no novo Windows. É possível desenvolver/testar o código antes disso.

## 3. Reconfigurar o painel no novo Windows

1. Copie `config/dashboard.example.json` para `config/dashboard.local.json`; o exemplo contém o escopo já provisionado do piloto.
2. Copie `config/repositories.example.json` para `config/repositories.json`. É uma seleção local, não o histórico do banco. Confira os aliases `personal/pilot` e as políticas antes de importar/coletar. Um vínculo confirmado sincroniza a seleção local; não inventar entradas por ID.
3. Configure a GitHub App existente **Dev Intelligence Fabrycio13**, App ID `5380709`, instalação piloto `173020772`. Não criar outra App nem ampliar permissões. O GitHub CLI serve ao desenvolvimento; seu token NÃO substitui a credencial da App no coletor.
4. Obtenha uma chave privada por procedimento seguro da App e configure-a localmente. Se a chave anterior só existe como DPAPI no outro PC, gere uma nova chave da MESMA App pelo GitHub, mantendo a anterior ativa. Não revogar a chave do PC de origem como parte da mudança. Guarde o PEM fora do checkout, em diretório protegido; o código também aceita chave criptografada DPAPI criada no novo usuário Windows. Copie `config/app.example.json` para `config/app.local.json` e ajuste `private_key_path` para o arquivo local protegido. Não envie chave pelo chat/Git.
5. Obtenha o certificado CA público do projeto Supabase e o endpoint real pelo painel **Connect** do projeto `cpkgvpuivjzplerzlpxz` (**Project GitHub**, não Usabit People). Não adivinhar a região do pooler. O endpoint pode depender da rede do PC do trabalho; TLS permanece `verify-full`.
6. Em um terminal LOCAL VISÍVEL, execute o setup abaixo substituindo apenas endpoint e caminho do CA. Digite a senha atual do worker no prompt oculto. Não usar senha administrativa, não resetar senha apenas por mudar de PC e não colar senha no comando/chat.

```bash
uv run python -m github_intelligence.db_setup \
  --host 'ENDPOINT_REAL_DO_CONNECT' \
  --project-ref cpkgvpuivjzplerzlpxz \
  --sslrootcert 'C:/caminho-local/prod-ca-2021.crt'
uv run github-intelligence db check
```

O setup valida primeiro e salva a senha cifrada no novo Windows uma única vez. Se não souber a senha atual do worker, parar e resolver pelo canal seguro; o Git não a contém. Falha de certificado/rede não prova senha errada.

7. Com banco e App configurados, iniciar:

```bash
uv run python -m github_intelligence.dashboard
```

Abrir `http://127.0.0.1:8765` e conferir os dados existentes. Abrir tela/relatório é somente leitura. **Adicionar repositório → verificar → confirmar vínculo** grava apenas o cadastro; **Atualizar agora** é outra ação e pede confirmação para coletar/gravar.

## Regras para continuar com outro agente

- Ler este documento, `docs/repository-onboarding.md` e o código antes de alterar.
- Não reaplicar 0001/0002/0003, não reprovisionar o piloto e não refazer senha/App sem necessidade comprovada.
- Nunca commit/push automáticos sem nova solicitação do usuário; não criar branches por conta própria.
- Nenhuma escrita remota de teste sem aprovação explícita. Não sobrescrever histórico e não inferir deploy a partir de merge.
- Configuração de cliente/projeto é fixa no painel local; GUC/SET ROLE são confiança interna do worker, não autenticação de usuário final.
- Preservar identidade revalidada e os locks até commit. Runtime não recebe UPDATE em clientes/projetos/instalações, membership de owner ou EXECUTE direto pelo login; executar regressões ao tocar nesses limites.
