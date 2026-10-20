# GitHub App — piloto pessoal

## Configuração efetivamente autorizada em 19/10/2026

- Nome: **Dev Intelligence Fabrycio13**.
- App ID: `5380709`.
- Instalação: `173020772`, conta `Fabrycio13`.
- Distribuição inicial: **Only on this account**. Clientes externos ainda exigirão habilitar instalação por outras contas; isso não foi feito silenciosamente no piloto.
- Repositórios selecionados: `Fabrycio13/project-mds` (ID `1226750215`) e `Fabrycio13/skills` (ID `1410914950`, privado).
- `repository_selection=selected`, confirmado também pela API da instalação.
- Permissões efetivas: `checks`, `contents`, `metadata`, `pull_requests` e `statuses`, **todas read**.
- Sem Administration, Deployments, Actions ou permissões organizacionais. Sem webhook e sem autorização OAuth do usuário durante instalação.
- Acesso privado via token de instalação validado: consulta de metadados de `Fabrycio13/skills` retornou sucesso e `private=true`. Nenhum token foi impresso ou persistido.

Esses identificadores não são segredos. A senha e o segundo fator usados no GitHub foram fornecidos pelo formulário seguro, nunca pelo chat/código do projeto. A chave gerada foi retirada de Downloads, criptografada com **DPAPI no escopo do usuário Windows atual** e mantida fora do repositório, em diretório com ACL restrita à conta atual e SYSTEM. A descriptografia foi comparada à chave original em memória antes da remoção do PEM. Os arquivos PEM gerados não estão mais presentes em Downloads nem no diretório protegido; não há promessa de apagamento forense de blocos do disco.

## Configuração local

`config/app.local.json` (ignorado pelo Git) contém somente:

- `app_id`;
- `installation_id`;
- `private_key_path`, referência ao arquivo protegido fora do projeto.

Nenhum PEM, token ou senha deve ser incluído no JSON. A seleção em `config/repositories.json` é separada da autorização na instalação. Acrescentar um privado na seleção local não concede acesso a ele.

## Administração no GitHub

Para gerenciar o acesso, usar **Settings → Applications → Installed GitHub Apps → Dev Intelligence Fabrycio13 → Configure**. A configuração deve permanecer em **Only select repositories**. Adicionar/remover alvos aqui altera autorização; desativar/remover no nosso CLI altera somente acompanhamento.

Para chave/permissões da App: **Settings → Developer settings → GitHub Apps → Dev Intelligence Fabrycio13**. Não gerar client secret: a POC usa JWT da App e token efêmero da instalação, não OAuth de usuário.

GitHub disponibiliza leitura de recursos públicos conforme suas próprias regras, mesmo em instalações com seleção restrita; a seleção limita os privados autorizados e o acompanhamento local deve continuar explícito.

## Gates concluídos

1. Chave criptografada no Windows com DPAPI; PEM gerado ausente em Downloads e no diretório protegido. ACL lida de volta e restrita ao usuário atual e SYSTEM.
2. CLI carregou referências locais e autenticou sem segredo no comando.
3. `repos available` retornou os dois selecionados, incluindo o privado.
4. `repos add Fabrycio13/skills` confirmou identidade e registro ativo; `repos list` confirmou os dois vínculos ativos.
5. Relatório real autenticado para ambos gerado com exit 0 e lido: `reports/pilot-app-dpapi.md`, intervalo `[2026-10-01T00:00:00Z, 2026-10-19T12:56:31.632885Z)`. Contém quatro PRs na amostra (um no público, três no privado), com fontes, sem marca de parcial.
6. Suite completa: **275 testes passando**, mypy sem erros em 11 arquivos, Ruff aprovado.

**Limitação de proteção local:** DPAPI não impede outro processo executado como o mesmo usuário Windows de descriptografar a chave. O material descriptografado existe em memória durante a assinatura; bytes imutáveis de Python não têm limpeza garantida. Produção e outros usuários/servidores exigem estratégia própria de gestão de segredos.

Nenhum comentário, aprovação, merge, push ou alteração de conteúdo/configuração dos repositórios foi realizado pelo coletor.
