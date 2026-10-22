"""Loopback-only MVP dashboard. No scheduler and no automatic remote writes."""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import ValidationError

from github_intelligence.config import ConfigurationError
from github_intelligence.github import GitHubError
from github_intelligence.models import ReportWindow
from github_intelligence.onboarding import OnboardingError, RepositoryOnboarding
from github_intelligence.repositories import RegistryError, RepositoryRegistry
from github_intelligence.storage import ProjectScope, StorageError

_ASSETS = Path(__file__).parent / "dashboard_assets"


class Store(Protocol):
    def list_repositories(self) -> list[dict[str, Any]]: ...
    def search_repositories(self, *, query: str, limit: int = 10) -> list[dict[str, Any]]: ...
    def get_report(self, report_id: UUID) -> dict[str, Any] | None: ...
    def list_reports(
        self, *, repository_id: UUID | None = None, limit: int = 20, offset: int = 0,
        include_options: bool = True,
    ) -> dict[str, Any]: ...
    def get_report_details(self, report_id: UUID) -> dict[str, Any] | None: ...
    def get_dashboard(self) -> dict[str, Any]: ...


class DashboardError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class DashboardApp:
    def __init__(
        self, store: Store, scope: ProjectScope, *,
        refresh: Callable[[str, ReportWindow], None],
        onboarding: RepositoryOnboarding | None = None,
    ) -> None:
        self.store = store
        self.scope = scope
        self.refresh = refresh
        self.onboarding = onboarding
        self.token = secrets.token_urlsafe(32)
        self.lock = Lock()

    def update(self, payload: object) -> None:
        if not isinstance(payload, dict) or set(payload) != {"repository_id", "since", "until"}:
            raise DashboardError(400, "Informe repositório e intervalo com timezone.")
        try:
            repository_id = str(UUID(payload["repository_id"]))
            window = ReportWindow(since=payload["since"], until=payload["until"])
        except (ValueError, TypeError, ValidationError, AttributeError):
            raise DashboardError(400, "Repositório ou intervalo inválido.") from None
        if window.until > datetime.now(timezone.utc):
            raise DashboardError(400, "O fim do intervalo não pode estar no futuro.")
        if not self.lock.acquire(blocking=False):
            raise DashboardError(409, "Uma coleta já está em andamento. Aguarde.")
        try:
            repo = next((r for r in self.store.list_repositories() if r["id"] == repository_id), None)
            if repo is None:
                raise DashboardError(404, "Repositório não encontrado neste projeto.")
            if repo["status"] != "active" or repo["installation_status"] != "active":
                raise DashboardError(409, "Repositório ou instalação não está ativo.")
            if repo.get("last_status") in {"pending", "running"}:
                raise DashboardError(409, "Este repositório já tem uma coleta em andamento.")
            self.refresh(repo["full_name"], window)
        finally:
            self.lock.release()


def cli_refresh(
    root: Path, scope: ProjectScope, client: str, project: str,
) -> Callable[[str, ReportWindow], None]:
    def refresh(name: str, window: ReportWindow) -> None:
        command = [
            sys.executable, "-m", "github_intelligence", "--config", str(root / "config/repositories.json"),
            "--database-config", str(root / "config/database.local.json"),
            "--client", client, "--project", project, "db", "import", "--repo", name,
            "--client-id", str(scope.client_id), "--project-id", str(scope.project_id),
            "--since", window.since.isoformat(), "--until", window.until.isoformat(), "--apply",
        ]
        try:
            result = subprocess.run(command, cwd=root, capture_output=True, timeout=300, check=False)
        except subprocess.TimeoutExpired:
            raise DashboardError(
                504, "Coleta excedeu o prazo. Confira a última execução antes de repetir."
            ) from None
        if result.returncode != 0:
            raise DashboardError(502, "A coleta falhou. Confira a conexão e o status da última execução.")
    return refresh


def make_server(app: DashboardApp, *, port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def send(
            self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
            )
            self.end_headers()
            self.wfile.write(body)

        def json(self, status: int, value: object) -> None:
            self.send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

        def check_host(self) -> str:
            assert isinstance(self.server, ThreadingHTTPServer)
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != origin.removeprefix("http://"):
                raise DashboardError(403, "Acesso permitido apenas pelo endereço local do painel.")
            return origin

        def do_GET(self) -> None:
            try:
                self.check_host()
                try:
                    if len(self.path) > 4096:
                        raise ValueError
                    url = urlsplit(self.path)
                    path = url.path
                    params = parse_qs(
                        url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=10,
                    )
                    allowed = {
                        "/api/reports": {"repository_id", "limit", "offset", "include_options"},
                        "/api/repositories/search": {"q", "limit"},
                        "/app.css": {"v"}, "/app.js": {"v"},
                    }.get(path, set())
                    if url.scheme or url.netloc or url.fragment or set(params) - allowed:
                        raise ValueError
                    if any(len(values) != 1 for values in params.values()):
                        raise ValueError
                except ValueError:
                    raise DashboardError(400, "Parâmetros de consulta inválidos.") from None
                static_paths = {
                    "/", "/app.js", "/app.css", "/bg.jpg", "/bg.webp",
                    "/logo.png", "/logo-icon.png", "/logo.svg", "/logo-icon.svg",
                }
                if path in static_paths:
                    image_paths = {
                        "/bg.jpg", "/bg.webp", "/logo.png",
                        "/logo-icon.png", "/logo.svg", "/logo-icon.svg",
                    }
                    if path in image_paths:
                        file_map = {
                            "/bg.jpg": ("bg.jpg", "image/jpeg"),
                            "/bg.webp": ("bg.webp", "image/webp"),
                            "/logo.png": ("logo.png", "image/png"),
                            "/logo-icon.png": ("logo-icon.png", "image/png"),
                            "/logo.svg": ("logo.svg", "image/svg+xml"),
                            "/logo-icon.svg": ("logo-icon.svg", "image/svg+xml"),
                        }
                        filename, content_type = file_map[path]
                        image_file = _ASSETS / filename
                        if image_file.exists():
                            self.send(200, image_file.read_bytes(), content_type)
                            return
                        raise DashboardError(404, "Arquivo não encontrado.")
                    name, content_type = {
                        "/": ("index.html", "text/html; charset=utf-8"),
                        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                        "/app.css": ("app.css", "text/css; charset=utf-8"),
                    }[path]
                    text = (_ASSETS / name).read_text(encoding="utf-8")
                    self.send(200, text.replace("__REQUEST_TOKEN__", app.token).encode("utf-8"), content_type)
                elif path == "/favicon.ico":
                    self.send(204, b"", "image/x-icon")
                elif path == "/api/repositories":
                    self.json(200, {
                        "repositories": app.store.list_repositories(), "updating": app.lock.locked(),
                    })
                elif path == "/api/repositories/search":
                    try:
                        query = params.get("q", [""])[0]
                        limit_text = params.get("limit", ["10"])[0]
                        if (not query.strip() or len(query) > 200 or "\x00" in query
                                or not limit_text.isascii() or not limit_text.isdigit()):
                            raise ValueError
                        limit = int(limit_text)
                        if not 1 <= limit <= 10:
                            raise ValueError
                    except ValueError:
                        raise DashboardError(400, "Busca ou limite inválidos.") from None
                    self.json(200, {"repositories": app.store.search_repositories(query=query, limit=limit)})
                elif path == "/api/reports":
                    try:
                        limit_text = params.get("limit", ["20"])[0]
                        offset_text = params.get("offset", ["0"])[0]
                        if not all(text.isascii() and text.isdigit() for text in (limit_text, offset_text)):
                            raise ValueError
                        limit, offset = int(limit_text), int(offset_text)
                        if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
                            raise ValueError
                        repository_id = (
                            UUID(params["repository_id"][0]) if "repository_id" in params else None
                        )
                        options_text = params.get("include_options", ["true"])[0]
                        if options_text not in {"true", "false"}:
                            raise ValueError
                    except ValueError:
                        raise DashboardError(400, "Filtro ou paginação inválidos.") from None
                    self.json(200, app.store.list_reports(
                        repository_id=repository_id, limit=limit, offset=offset,
                        include_options=options_text == "true",
                    ))
                elif path == "/api/dashboard":
                    self.json(200, app.store.get_dashboard())
                elif path.startswith("/api/reports/"):
                    details = path.endswith("/details")
                    identifier = path.removeprefix("/api/reports/")
                    if details:
                        identifier = identifier.removesuffix("/details")
                    try:
                        report_id = UUID(identifier)
                    except ValueError:
                        raise DashboardError(400, "Identificador de relatório inválido.") from None
                    report = (
                        app.store.get_report_details(report_id)
                        if details else app.store.get_report(report_id)
                    )
                    if report is None:
                        raise DashboardError(404, "Relatório não encontrado neste projeto.")
                    self.json(200, report)
                else:
                    raise DashboardError(404, "Página não encontrada.")
            except DashboardError as error:
                self.json(error.status, {"error": str(error)})
            except (StorageError, ConfigurationError, OSError):
                self.json(503, {"error": "Não foi possível carregar os dados. Verifique a conexão local."})

        def do_POST(self) -> None:
            try:
                origin = self.check_host()
                if (self.headers.get("Origin") != origin
                        or not secrets.compare_digest(self.headers.get("X-Request-Token", ""), app.token)):
                    raise DashboardError(403, "Pedido recusado. Reabra o painel local.")
                if self.path not in {"/api/update", "/api/repositories/inspect", "/api/repositories/bind"}:
                    raise DashboardError(404, "Operação não encontrada.")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise DashboardError(400, "Formato de pedido inválido.")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    raise DashboardError(400, "Pedido inválido ou grande demais.") from None
                if self.path == "/api/update":
                    app.update(payload)
                    self.json(200, {"message": "Coleta concluída. Confira o status e o relatório abaixo."})
                elif app.onboarding is None:
                    raise DashboardError(503, "Vinculação de repositórios não configurada neste painel.")
                elif self.path == "/api/repositories/inspect":
                    self.json(200, app.onboarding.inspect(payload))
                else:
                    if not app.lock.acquire(blocking=False):
                        raise DashboardError(409, "Outra operação está em andamento. Aguarde.")
                    try:
                        self.json(200, app.onboarding.bind(payload))
                    finally:
                        app.lock.release()
            except (DashboardError, OnboardingError) as error:
                self.json(error.status, {"error": str(error)})
            except GitHubError:
                self.json(502, {"error": "Não foi possível verificar o GitHub. Tente novamente mais tarde."})
            except (StorageError, ConfigurationError, RegistryError, OSError):
                self.json(503, {"error": "Não foi possível atualizar. Verifique a conexão local."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    from github_intelligence.config import AppSettings
    from github_intelligence.dashboard_data import DashboardStore
    from github_intelligence.database import DatabaseSettings, connect_database
    from github_intelligence.db_health import check_database
    from github_intelligence.repository_access import RepositoryAccess
    from github_intelligence.repository_binding import RepositoryBinder

    parser = argparse.ArgumentParser(description="Painel local do GitHub Intelligence")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scope-config", type=Path, default=Path("config/dashboard.local.json"))
    args = parser.parse_args()
    try:
        config = json.loads(args.scope_config.read_text(encoding="utf-8"))
        scope = ProjectScope(client_id=config["client_id"], project_id=config["project_id"])
        root = Path.cwd().resolve()
        settings = DatabaseSettings.from_environment(config_path=root / "config/database.local.json")
        with connect_database(settings) as connection:
            check_database(connection)
        store = DashboardStore(lambda: connect_database(settings), scope)
        onboarding = RepositoryOnboarding(
            RepositoryAccess(AppSettings.from_environment(config_path=root / "config/app.local.json")),
            RepositoryBinder(lambda: connect_database(settings), scope),
            RepositoryRegistry(root / "config/repositories.json"),
            client=config["client"], project=config["project"],
        )
        app = DashboardApp(
            store, scope, refresh=cli_refresh(root, scope, config["client"], config["project"]),
            onboarding=onboarding,
        )
        server = make_server(app, port=args.port)
    except (OSError, ValueError, KeyError, StorageError, ConfigurationError):
        print("Não foi possível iniciar o painel. Confira a configuração local e db check.")
        return 2
    print(f"Painel local: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
