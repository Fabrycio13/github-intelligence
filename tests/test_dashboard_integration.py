"""Synthetic HTTP update to committed rows in real disposable PostgreSQL."""
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Thread

import httpx
from test_storage import prepared_db as prepared_db

from github_intelligence.dashboard import DashboardApp, make_server
from github_intelligence.models import ReportWindow, RepositoryConfig, RepositorySnapshot
from github_intelligence.persistence import persist_repository_snapshot
from github_intelligence.storage import ProjectScope


def test_http_update_persists_report_and_returns_it_through_the_dashboard(prepared_db):
    from github_intelligence.dashboard_data import DashboardStore

    db, ids = prepared_db
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])

    @contextmanager
    def connection():
        yield db

    def refresh(name: str, window: ReportWindow) -> None:
        assert name == "owner/repo-a"
        snapshot = RepositorySnapshot(
            config=RepositoryConfig(full_name=name, github_id=1001),
            github_id=1001, authenticated=True,
            observed_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        )
        persist_repository_snapshot(
            db, scope, snapshot, window, "# SYNTHETIC dashboard report",
            github_app_id=1, github_installation_id=1,
        )

    app = DashboardApp(DashboardStore(connection, scope), scope, refresh=refresh)
    server = make_server(app, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            before = client.get("/api/repositories").json()["repositories"]
            assert len(before) == 1
            assert before[0]["last_status"] is None
            headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
            response = client.post("/api/update", headers=headers, json={
                "repository_id": str(ids["repo_a"]),
                "since": "2026-10-01T00:00:00Z", "until": "2026-10-19T00:00:00Z",
            })
            assert response.status_code == 200
            after = client.get("/api/repositories").json()["repositories"]
            assert after[0]["last_status"] == "succeeded"
            report = client.get("/api/reports/" + after[0]["report_id"])
            assert report.status_code == 200
            assert report.json()["content"] == "# SYNTHETIC dashboard report"
            assert report.json()["complete"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
