"""Terminal POC. GitHub access is read-only; database writes require explicit --apply."""

import argparse
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Sequence
from uuid import UUID

from pydantic import ValidationError

from github_intelligence.config import AppSettings, ConfigurationError
from github_intelligence.models import ReportWindow, RepositoryConfig, RepositorySnapshot
from github_intelligence.repositories import RegistryError, RepositoryRegistry
from github_intelligence.storage import StorageError

if TYPE_CHECKING:
    from github_intelligence.github import GitHubClient


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ConfigurationError("Argumentos inválidos; consulte --help.")


def _parser() -> Parser:
    parser = Parser(description="GitHub Intelligence — POC somente leitura")
    parser.add_argument("--config", type=Path, default=Path("config/repositories.json"))
    parser.add_argument("--client", default="personal", help="Escopo local do cliente (não é autenticação)")
    parser.add_argument("--project", default="pilot")
    parser.add_argument("--database-config", type=Path, help="Configuração não secreta do PostgreSQL")
    parser.add_argument(
        "--public", action="store_true", help="Acesso anônimo explícito, apenas dados públicos"
    )
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    database = commands.add_parser("db", help="Diagnóstico e importação explícita no PostgreSQL")
    database_commands = database.add_subparsers(dest="database_command", required=True, parser_class=Parser)
    database_commands.add_parser(
        "check", help="Verificar conexão TLS, papel runtime e isolamento sem contexto"
    )
    database_import = database_commands.add_parser(
        "import", help="Planejar ou persistir uma coleta GitHub em um projeto já provisionado"
    )
    database_import.add_argument("--repo", required=True, help="OWNER/REPO ativo na seleção local")
    database_import.add_argument(
        "--client-id", required=True, type=UUID, help="UUID do cliente no PostgreSQL"
    )
    database_import.add_argument(
        "--project-id", required=True, type=UUID, help="UUID do projeto no PostgreSQL"
    )
    database_import.add_argument("--since", required=True, help="Início inclusivo com timezone")
    database_import.add_argument("--until", required=True, help="Fim exclusivo com timezone")
    database_import.add_argument(
        "--apply", action="store_true", help="Executar coleta e gravar; sem esta opção só mostra o plano"
    )
    repos = commands.add_parser("repos", help="Selecionar e gerenciar acompanhamento")
    repo_commands = repos.add_subparsers(dest="repo_command", required=True, parser_class=Parser)
    repo_commands.add_parser("list", help="Mostrar seleções locais, inclusive inativas/removidas")
    repo_commands.add_parser("available", help="Listar repositórios autorizados na instalação GitHub App")
    add = repo_commands.add_parser("add", help="Verificar acesso e selecionar repositório")
    add.add_argument("repository")
    add.add_argument("--staging-branch")
    add.add_argument("--required-approvals", type=int, default=2)
    for action in ("activate", "deactivate", "remove"):
        repo_commands.add_parser(action).add_argument("repository")
    report = commands.add_parser("report", help="Coletar e salvar relatório Markdown")
    report.add_argument("--repo", action="append", default=[], help="Seleção ativa OWNER/REPO; repetível")
    report.add_argument("--since", required=True, help="Início inclusivo com timezone")
    report.add_argument(
        "--until", required=True, help="Fim exclusivo com timezone; snapshot é o estado atual"
    )
    report.add_argument(
        "--output", required=True, type=Path, help="Arquivo novo; histórico não é sobrescrito"
    )
    return parser


def _client(public: bool, *, installation_id: int | None = None) -> "GitHubClient":
    from github_intelligence.auth import installation_token
    from github_intelligence.github import GitHubClient

    if public:
        return GitHubClient()
    settings = AppSettings.from_environment(config_path=Path("config/app.local.json"))
    token = installation_token(
        settings.app_id, settings.private_key,
        installation_id if installation_id is not None else settings.installation_id,
    )
    return GitHubClient(token)


def _window(since: str, until: str) -> ReportWindow:
    try:
        window = ReportWindow(since=since, until=until)  # type: ignore[arg-type]
    except ValidationError:
        raise ConfigurationError(
            "Intervalo inválido: use since < until com timezone, por exemplo 2026-10-01T00:00:00Z."
        ) from None
    if window.until > datetime.now(timezone.utc):
        raise ConfigurationError("O fim do intervalo não pode estar no futuro.")
    return window


def _scope(registry: RepositoryRegistry, args: argparse.Namespace) -> list[RepositoryConfig]:
    return [
        repo
        for repo in registry.entries()
        if (repo.client_id, repo.project_id) == (args.client, args.project)
    ]


def _repos(args: argparse.Namespace, registry: RepositoryRegistry) -> int:
    if args.repo_command == "list":
        entries = _scope(registry, args)
        for repo in entries:
            identity = str(repo.github_id) if repo.github_id else "não verificado"
            print(f"{repo.full_name}\t{repo.status}\tGitHub ID: {identity}")
        if not entries:
            print("Nenhum repositório selecionado neste cliente/projeto.")
        return 0
    if args.repo_command in {"activate", "deactivate", "remove"}:
        status = {"activate": "active", "deactivate": "inactive", "remove": "removed"}[args.repo_command]
        registry.set_status(args.repository, status, client_id=args.client, project_id=args.project)
        print(f"{args.repository}: {status}. Histórico existente preservado; GitHub não foi alterado.")
        return 0
    if args.repo_command == "available":
        if args.public:
            raise ConfigurationError("Listar repositórios autorizados requer GitHub App, sem --public.")
        with _client(False) as client:
            for item in client.paginate("/installation/repositories", key="repositories"):
                name = item.get("full_name")
                if not isinstance(name, str):
                    raise ConfigurationError("GitHub retornou identificação incompleta.")
                validated = RepositoryConfig(full_name=name)
                print(validated.full_name)
        return 0
    entry = RepositoryConfig(
        full_name=args.repository,
        client_id=args.client,
        project_id=args.project,
        staging_branch=args.staging_branch,
        required_approvals=args.required_approvals,
    )
    with _client(args.public) as client:
        data = client.get(f"/repos/{entry.full_name}")
        if not isinstance(data, dict) or not isinstance(data.get("id"), int):
            raise ConfigurationError("GitHub não confirmou a identidade do repositório.")
        if (
            not isinstance(data.get("full_name"), str)
            or data["full_name"].casefold() != entry.full_name.casefold()
        ):
            raise ConfigurationError("A identidade retornada diverge do repositório solicitado.")
        if args.public and data.get("private") is not False:
            raise ConfigurationError("O modo público não autoriza acesso privado.")
        entry = entry.model_copy(update={"github_id": data["id"]})
    registry.add(entry)
    print(f"{entry.full_name}: selecionado e ativo. Nenhum backfill ou envio automático iniciado.")
    return 0


def _report(args: argparse.Namespace, registry: RepositoryRegistry) -> int:
    from github_intelligence.collect import collect_repository
    from github_intelligence.github import GitHubError
    from github_intelligence.report import render_report

    window = _window(args.since, args.until)
    if args.output.exists():
        raise ConfigurationError(
            "O arquivo de saída já existe. Escolha outro nome para preservar o histórico."
        )
    requested = [RepositoryConfig(full_name=name).full_name.casefold() for name in args.repo]
    active = registry.active(client_id=args.client, project_id=args.project)
    selected = [repo for repo in active if not requested or repo.full_name.casefold() in requested]
    if not selected or set(requested) - {repo.full_name.casefold() for repo in selected}:
        raise ConfigurationError(
            "Selecione um repositório ativo neste cliente/projeto; consulte repos list/add."
        )
    snapshots: list[RepositorySnapshot] = []
    with ExitStack() as stack:
        clients: dict[int | None, "GitHubClient"] = {}
        for repo in selected:
            selected_installation = None if args.public else repo.github_installation_id
            if selected_installation not in clients:
                source = (
                    _client(args.public, installation_id=selected_installation)
                    if selected_installation is not None else _client(args.public)
                )
                clients[selected_installation] = stack.enter_context(source)
            client = clients[selected_installation]
            current = {
                item.full_name.casefold(): item
                for item in registry.active(client_id=args.client, project_id=args.project)
            }
            if repo.full_name.casefold() not in current:
                snapshots.append(
                    RepositorySnapshot(
                        config=repo,
                        observed_at=datetime.now(timezone.utc),
                        authenticated=client.authenticated,
                        complete=False,
                        limitations=["Acompanhamento desativado/removido antes da coleta; não executado."],
                    )
                )
                continue
            try:
                snapshot = collect_repository(client, current[repo.full_name.casefold()], window)
            except (GitHubError, ValueError):
                snapshot = RepositorySnapshot(
                    config=repo,
                    observed_at=datetime.now(timezone.utc),
                    authenticated=client.authenticated,
                    complete=False,
                    limitations=[
                        "Falha de acesso/coleta no GitHub. Nenhuma ausência de atividade pode ser concluída."
                    ],
                )
            snapshots.append(snapshot)
    # Recheck selections before publishing: do not include newly disabled repository content.
    current = {
        repo.full_name.casefold(): repo
        for repo in registry.active(client_id=args.client, project_id=args.project)
    }
    for index, snapshot in enumerate(snapshots):
        name = snapshot.config.full_name.casefold()
        if name not in current:
            snapshots[index] = snapshot.model_copy(
                update={
                    "pulls": [],
                    "complete": False,
                    "limitations": [
                        "Acompanhamento desativado/removido durante execução; conteúdo não publicado."
                    ],
                }
            )
    content = render_report(snapshots, window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(content)
    partial = any(not snapshot.complete for snapshot in snapshots)
    print(f"Relatório {'PARCIAL' if partial else 'gerado'}: {args.output}")
    return 1 if partial else 0


def _database_check(args: argparse.Namespace) -> int:
    from github_intelligence.database import DatabaseSettings, connect_database
    from github_intelligence.db_health import check_database

    config_path = args.database_config
    if config_path is None:
        default = Path("config/database.local.json")
        if default.exists():
            config_path = default
    settings = DatabaseSettings.from_environment(config_path=config_path)
    with connect_database(settings) as connection:
        health = check_database(connection)
    print(
        f"Banco verificado: {health.database}; login restrito: {health.login_role}; "
        f"{health.tables_checked} tabelas conferidas. TLS verify-full. Nenhum dado importado."
    )
    return 0


def _database_import(args: argparse.Namespace, registry: RepositoryRegistry) -> int:
    from github_intelligence.collect import collect_repository
    from github_intelligence.database import DatabaseSettings, connect_database
    from github_intelligence.db_health import check_database
    from github_intelligence.github import GitHubError
    from github_intelligence.persistence import persist_repository_snapshot
    from github_intelligence.report import render_report
    from github_intelligence.storage import ProjectScope

    window = _window(args.since, args.until)
    requested = RepositoryConfig(full_name=args.repo)
    selected = [
        repo
        for repo in registry.active(client_id=args.client, project_id=args.project)
        if repo.full_name.casefold() == requested.full_name.casefold()
    ]
    if len(selected) != 1:
        raise ConfigurationError("Selecione exatamente um repositório ativo neste cliente/projeto local.")
    repository_config = selected[0]
    if repository_config.github_id is None:
        raise ConfigurationError(
            "O repositório precisa ter identidade GitHub verificada antes da importação."
        )

    if not args.apply:
        print(
            f"Plano sem escrita: {repository_config.full_name}; cliente {args.client_id}; "
            f"projeto {args.project_id}; janela [{window.since.isoformat()}, {window.until.isoformat()}). "
            "Nenhum dado foi coletado, conectado ou gravado."
        )
        return 0

    app_settings = AppSettings.from_environment(config_path=Path("config/app.local.json"))
    selected_installation = repository_config.github_installation_id
    try:
        github_client = (
            _client(False, installation_id=selected_installation)
            if selected_installation is not None else _client(False)
        )
        with github_client as github:
            snapshot = collect_repository(github, repository_config, window)
    except (GitHubError, ValueError):
        raise ConfigurationError("Coleta GitHub falhou; nenhuma gravação no banco foi tentada.") from None

    content = render_report([snapshot], window)
    config_path = args.database_config
    if config_path is None:
        default = Path("config/database.local.json")
        if default.exists():
            config_path = default
    database_settings = DatabaseSettings.from_environment(config_path=config_path)
    with connect_database(database_settings) as connection:
        check_database(connection)
        result = persist_repository_snapshot(
            connection,
            ProjectScope(client_id=args.client_id, project_id=args.project_id),
            snapshot,
            window,
            content,
            github_app_id=int(app_settings.app_id),
            github_installation_id=(
                selected_installation if selected_installation is not None else app_settings.installation_id
            ),
        )
    print(
        f"Importação {result.status}: {repository_config.full_name}; "
        f"{len(result.snapshot_ids)} snapshots; sync {result.sync_run_id}; report {result.report_id}."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from github_intelligence.github import GitHubError

    try:
        args = _parser().parse_args(argv)
        if args.command == "db":
            if args.database_command == "check":
                return _database_check(args)
            registry = RepositoryRegistry(args.config)
            return _database_import(args, registry)
        registry = RepositoryRegistry(args.config)
        return _repos(args, registry) if args.command == "repos" else _report(args, registry)
    except (ConfigurationError, RegistryError, GitHubError, StorageError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    except ValidationError:
        print(
            "Erro: configuração ou resposta inválida; verifique identificadores e campos obrigatórios.",
            file=sys.stderr,
        )
        return 2
    except OSError:
        print("Erro: falha ao ler/gravar arquivo local. Verifique caminho e permissões.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
