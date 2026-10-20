"""HTTP onboarding with real PostgreSQL and a synthetic read-only GitHub provider."""
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import httpx
from test_database_health import runtime_database as runtime_database
from test_onboarding import Access, confirmation
from test_storage import prepared_db as prepared_db

from github_intelligence.dashboard import DashboardApp, make_server
from github_intelligence.dashboard_data import DashboardStore
from github_intelligence.onboarding import RepositoryOnboarding
from github_intelligence.repositories import RepositoryRegistry
from github_intelligence.storage import ProjectScope


def test_confirm_creates_scoped_binding_but_never_starts_a_collection(runtime_database, tmp_path):
    from github_intelligence.repository_binding import RepositoryBinder

    db, runtime, ids = runtime_database
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])

    @contextmanager
    def connection():
        yield runtime

    registry = RepositoryRegistry(tmp_path / "repos.json")
    onboarding = RepositoryOnboarding(
        Access(), RepositoryBinder(connection, scope), registry, client="personal", project="pilot",
    )
    collections = []
    app = DashboardApp(
        DashboardStore(connection, scope), scope, refresh=lambda *args: collections.append(args),
        onboarding=onboarding,
    )
    server = make_server(app, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
            link = {"link": "https://github.com/owner/repo"}
            before = client.post("/api/repositories/inspect", headers=headers, json=link)
            assert before.status_code == 200
            assert before.json()["can_bind"] is False
            bind_payload = confirmation(link["link"])
            blocked = client.post("/api/repositories/bind", headers=headers, json=bind_payload)
            assert blocked.status_code == 503
            assert registry.entries() == []
            migration = Path(__file__).resolve().parents[1] / "migrations/0003_repository_onboarding.sql"
            db.execute(migration.read_text(encoding="utf-8"), prepare=False)
            bound = client.post("/api/repositories/bind", headers=headers, json=bind_payload)
            assert bound.status_code == 200, bound.text
            assert bound.json()["status"] == "bound"
            again = client.post("/api/repositories/bind", headers=headers, json=bind_payload)
            assert again.status_code == 200
            assert again.json()["repository"]["id"] == bound.json()["repository"]["id"]
            listing = client.get("/api/repositories").json()["repositories"]
            added = [row for row in listing if row["full_name"] == "owner/repo"]
            assert len(added) == 1
            assert added[0]["last_status"] is None
            assert len(registry.entries()) == 1
            assert registry.entries()[0].github_installation_id == 30
            assert db.execute("SELECT count(*) FROM github_intelligence.sync_runs").fetchone() == (0,)
            assert collections == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
