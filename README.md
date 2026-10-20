# GitHub Intelligence — POC

Coleta somente leitura do GitHub, avaliação determinística e relatório Markdown com evidências. A persistência PostgreSQL é opcional e só grava com `db import --apply`. Não é Jira, não faz review automático, não aprova PR, não executa merge e não presume deploy.

## Estado da entrega

O piloto tem CLI e painel local mínimo: repositórios provisionados, última coleta, relatório salvo e atualização manual confirmada. Ainda não há hospedagem, autenticação de usuários finais, scheduler, envio de e-mail ou integração com IA. A conexão e persistência em PostgreSQL/Supabase usam schema e papel runtime restritos; o painel é para o operador local, não uma aplicação multiusuário de produção.

O alvo inicial é `Fabrycio13/project-mds`. A API pública confirmou em 19/10/2026 que ele é público. A GitHub App foi instalada e o acesso privado a `Fabrycio13/skills` foi validado, inclusive pela CLI com chave criptografada; detalhes em `docs/github-app-setup.md`. Não há fallback anônimo silencioso: o modo público exige `--public`.

## Instalar e testar

**Continuar em outro PC:** siga [docs/continuar-em-outro-pc.md](docs/continuar-em-outro-pc.md). Configurações e credenciais deste Windows não são publicadas no Git; o documento registra o estado atual e os passos para retomada.

Para abrir o painel já configurado, execute `uv run python -m github_intelligence.dashboard` na raiz ou abra `start-dashboard.cmd`. O endereço é `http://127.0.0.1:8765`. Consultar a tela não inicia coleta; **Atualizar agora** pede confirmação antes de gravar. Configuração e limites em `docs/dashboard.md`.

Requisitos: Python >=3.11 e uv. Execute na raiz do projeto, em Git Bash:

```bash
uv sync --locked
uv run pytest -q
uv run mypy
uv run ruff check src tests
uv run github-intelligence --help
```

`uv.lock` fixa o ambiente resolvido. Não use o `pip` global, que pode apontar para outro Python. Para um checkout novo, copie `config/repositories.example.json` para `config/repositories.json` (a configuração real é local e ignorada pelo Git).

## Primeiro relatório público, sem credenciais

```bash
uv run github-intelligence --public repos list
uv run github-intelligence --public report \
  --repo Fabrycio13/project-mds \
  --since 2026-10-01T00:00:00Z \
  --until 2026-10-19T00:00:00Z \
  --output reports/project-mds-2026-10-19.md
```

Escolha intervalos passados com timezone. O intervalo é `[since, until)`: início incluso e fim exclusivo. Eventos são filtrados por seus timestamps; os estados representam a observação atual, NÃO uma reconstrução do estado no fim do intervalo. A primeira execução exige intervalo explícito. Ainda não há watermark incremental persistido.

A coleta é centrada em PRs, incluindo commits desses PRs. Não cobre todos os commits feitos diretamente nas branches, tarefas externas ou validação manual de homologação. Nenhum PR encontrado não comprova ausência de desenvolvimento. Sem PR real, o smoke valida acesso e geração do documento, não reviews/checks reais.

O arquivo de saída deve ser novo; não há sobrescrita de histórico. `reports/` é ignorado pelo Git e deve ficar em diretório protegido pelas permissões do sistema operacional.

## Persistência PostgreSQL/Supabase

`github-intelligence db check` valida conexão TLS e permissões sem gravar. `github-intelligence db import` sem `--apply` apenas valida a seleção local e imprime um plano: não conecta ao GitHub nem ao banco. Importação usa exatamente um repositório ativo por execução e exige UUIDs de cliente/projeto já provisionados no banco, além de instalação e identidade do repositório correspondentes. Falhas de coleta interrompem antes da conexão de escrita; falhas durante a persistência fazem rollback dos snapshots/relatório e tentam marcar a execução como `failed`.

Exemplo de plano (substitua UUIDs e intervalo; não grava):

```bash
uv run github-intelligence db import \
  --repo OWNER/REPO --client-id 00000000-0000-0000-0000-000000000001 \
  --project-id 00000000-0000-0000-0000-000000000002 \
  --since 2026-10-01T00:00:00Z --until 2026-10-19T00:00:00Z
```

Adicionar `--apply` coleta pelo GitHub App e persiste snapshots permitidos, relatório, fontes e trilha de auditoria no escopo informado. Faça essa gravação somente após revisar/aprovar o escopo e os dados. Não executamos importação remota nesta etapa. Consulte `docs/database-runtime.md` para os gates e limitações; clientes/projetos/vínculos ainda precisam estar provisionados antes do primeiro import.

## Selecionar vários repositórios

```bash
# Modo público apenas para repositórios públicos; confirma identidade antes de salvar.
uv run github-intelligence --public repos add Fabrycio13/project-mds

# Com a GitHub App configurada, sem --public:
uv run github-intelligence repos available
uv run github-intelligence repos add SEU_USUARIO/SEU_REPOSITORIO

# Gerenciar seleção local, sem alterar nada no GitHub:
uv run github-intelligence repos list
uv run github-intelligence repos deactivate Fabrycio13/project-mds
uv run github-intelligence repos activate Fabrycio13/project-mds
uv run github-intelligence repos remove Fabrycio13/project-mds
```

`deactivate` mantém seleção e arquivos históricos; `activate` retoma somente quando você executar nova coleta. `remove` marca o vínculo como removido, não apaga o registro nem o repositório. Não há backfill ou envio automático ao reativar. `add` readiciona/reativa sem duplicar identidade no mesmo escopo; identidade GitHub confirmada permite reconhecer renomeação. Repositório com nome reaproveitado e ID diferente não é substituído silenciosamente.

Use `--staging-branch NOME` e `--required-approvals N` ao adicionar para configurar referência. O número configurado é uma expectativa local, NÃO prova da regra efetiva do GitHub.

Um relatório pode selecionar várias entradas ativas repetindo `--repo`; omitindo a opção, usa todas as entradas ativas do escopo. Não existe limite artificial de repositórios; rate limits e tempo de execução continuam aplicáveis.

```bash
uv run github-intelligence --client personal --project pilot report \
  --repo SEU_USUARIO/REPO_A --repo SEU_USUARIO/REPO_B \
  --since 2026-10-01T00:00:00Z --until 2026-10-19T00:00:00Z \
  --output reports/pilot-2026-10-19.md
```

Flags globais (`--public`, `--config`, `--client`, `--project`) vêm ANTES do comando. Os nomes acima com `SEU_...` são exemplos, não repositórios criados.

Nesta POC, um relatório fica restrito a um cliente/projeto, e uma execução autenticada usa uma instalação GitHub App. Múltiplas instalações de clientes, sessões de usuário, RLS e trilha administrativa persistente são gates posteriores, não capacidades prontas.

## Configurar a GitHub App para testar um privado

1. No GitHub, crie uma GitHub App nas configurações de desenvolvedor da conta/organização responsável. A criação/instalação é uma ação explícita do titular, não realizada por esta POC.
2. Para polling de terminal, desative webhooks; não é necessário inventar um callback local ou hospedar uma interface agora. Não habilite permissões de escrita.
3. Permissões de repositório utilizadas: **Pull requests: Read-only**, **Contents: Read-only**, **Checks: Read-only**, **Commit statuses: Read-only** e **Metadata: Read-only**. Não solicitar Administration, Actions, e-mail ou permissões organizacionais nesta etapa.
4. Instale a App em **Only select repositories**, escolhendo os alvos. Inclua o privado desejado sem torná-lo público. Para instalações futuras por clientes, configure a distribuição da App para permitir contas externas, sujeita às políticas de cada organização.
5. Gere uma chave privada e mantenha o PEM fora do repositório em diretório protegido. Não envie a chave, tokens ou senha no chat.
6. Configure o App ID, o ID da instalação e o caminho absoluto para a chave no ambiente do processo:

```bash
export GITHUB_APP_ID='ID_DA_APP'
export GITHUB_INSTALLATION_ID='ID_DA_INSTALACAO'
export GITHUB_PRIVATE_KEY_PATH='C:/caminho-protegido/github-intelligence.private-key.pem'
uv run github-intelligence repos available
uv run github-intelligence repos add SEU_USUARIO/REPO_PRIVADO
```

Os valores acima são placeholders, não credenciais. O ID da instalação pode ser obtido na URL da configuração da instalação do GitHub. `.env.example` é somente referência: o programa NÃO carrega `.env` automaticamente. O token temporário é obtido em memória para a execução e não é salvo no JSON local.

Instalação é uma autorização GitHub, seleção local é outra decisão. O sistema não amplia a autorização recebida. Retirar acesso/desinstalar no GitHub difere de desativar o monitoramento localmente. Falta de permissão não é uma resposta vazia bem-sucedida.

### Configuração local do piloto no Windows

Além das variáveis de ambiente, a CLI pode carregar `config/app.local.json` na raiz de execução. Esse arquivo local é ignorado pelo Git e contém apenas referências não secretas:

```json
{
  "app_id": "ID_DA_APP",
  "installation_id": 123,
  "private_key_path": "C:/diretorio-protegido/app.dpapi"
}
```

Os valores são exemplos. Variáveis de ambiente explícitas prevalecem sobre o arquivo. A chave `.dpapi` é criptografada para o usuário Windows atual; não é um PEM nem um token em texto puro. O diretório deve ter ACL restrita. Essa proteção é local: não copie o arquivo para outro usuário/servidor esperando que funcione; em produção use o gerenciador de segredos definido para o ambiente. O modo `--public` continua explícito e não usa a App.

A configuração efetiva e os gates de acesso privado estão documentados em `docs/github-app-setup.md`. Não coloque chave, senha, client secret ou token no JSON, nos relatórios ou no chat.

### Endpoints e permissões

| Consulta | Permissão de leitura |
|---|---|
| Metadados e repositórios da instalação | Metadata |
| PRs, reviews e commits de PR | Pull requests |
| Check runs do head SHA | Checks |
| Commit statuses do head SHA | Commit statuses |
| Conteúdo/commits adicionais, quando usados | Contents |

Somente a troca de JWT por token de instalação usa POST. Nenhuma chamada escreve no conteúdo ou nas configurações dos repositórios. O cliente de coleta só expõe GET; não segue redirecionamentos com credenciais e limita paginação ao endpoint GitHub permitido.

## Estados e limitações explícitas

- Aprovações observadas são distintas de aprovações comprovadamente válidas. Não se usa associação do autor como prova de permissão.
- COMMENTED/PENDING não apagam automaticamente uma decisão; reviews dispensadas não contam como aprovações atuais.
- Uma review em SHA antigo não é automaticamente invalidada: isso depende da política e evidência.
- CI é observado no head coletado. Check opcional falhando não é prova de check obrigatório falhando. Sem regras completas, não se confirma elegibilidade.
- Esta POC não resolve todas as regras, CODEOWNERS, conversas pendentes, merge queue ou permissões específicas de revisores/atores. **Nunca declara elegibilidade confirmada para merge.**
- Merge observado não comprova o destino histórico pela base atual, nem deploy; branch de homologação precisa ser configurada e ter evidência histórica para confirmar integração. Não há coleta de deployments nem confirmação de homologação funcional nesta etapa.
- PR com head/base/estado alterado durante coleta é marcado inconsistente. Falhas de fontes produzem relatório parcial, sem transformar desconhecido em falso.
- Histórico atual de reviews não reconstrói todas as transições entre execuções. Dados ausentes e limites de cobertura são exibidos no documento.
- Conteúdo de repositório é dado não confiável: não executamos suas instruções, não renderizamos HTML bruto e não enviamos diffs a modelo.
- Esta POC não busca diffs/código-fonte. Metadados, títulos e nomes ainda podem ser confidenciais; a retenção e o acesso aos arquivos locais são responsabilidade do operador até a fase de persistência.

## Falhas e operação

- Exit **0**: execução concluída dentro da cobertura declarada; não significa deploy validado nem conformidade completa do MVP.
- Exit **1**: relatório parcial emitido; consulte as limitações.
- Exit **2**: argumentos, configuração, autenticação ou operação sem sucesso; não avance marco de execução.
- Retries e esperas são limitados; rate limit que exigir espera excessiva interrompe em vez de bloquear indefinidamente.
- Falha de um repositório não fabrica seu resultado nem elimina os demais.
- Configuração local é gravada com arquivo pendente e trava exclusiva entre escritores CLI. Se o processo cair e deixar `.lock`/`.pending`, confirme que não há execução antes de removê-los manualmente. Não há edição concorrente suportada por editor externo.
- Relatórios locais são evidência da execução, não um histórico transacional completo de produção. Nenhum token, chave ou body de erro da API deve ser impresso.

## Arquitetura

```text
CLI/configuração → GitHub App ou público explícito → coleta → snapshot tipado
                                                        ↓
                                        avaliação determinística → Markdown
```

Módulos: `models`, `config`, `repositories`, `auth`, `github`, `collect`, `evaluate`, `report`, `storage`, `persistence`, `db_health`, `__main__`. O núcleo de coleta/avaliação será reaproveitado no FastAPI. Agendador, autenticação de usuários, autorização de endpoints, IA e e-mail entram em entregas posteriores.

## Referências oficiais

Consultadas em 19/10/2026. A versão REST explicitamente usada é `2026-03-10`, conforme exemplos atuais da documentação GitHub; conferir compatibilidade antes de atualizar.

- https://docs.github.com/en/rest/pulls/pulls
- https://docs.github.com/en/rest/pulls/reviews
- https://docs.github.com/en/rest/apps/apps
- https://docs.github.com/en/rest/checks/runs
- https://docs.github.com/en/rest/commits/statuses
- https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps
- https://docs.github.com/en/apps/using-github-apps/installing-a-github-app-from-a-third-party
