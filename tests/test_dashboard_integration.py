"""Synthetic HTTP update to committed rows in real disposable PostgreSQL."""
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Thread
from uuid import UUID

import httpx
import psycopg
from test_dashboard_data import _report, _run
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


def test_http_read_only_history_survives_latest_failed_sync_and_removed_repository(prepared_db):
    from github_intelligence.dashboard_data import DashboardStore

    db, ids = prepared_db
    _run(db, ids, UUID(int=1), "2026-10-18Z")
    for n in range(10, 17):
        _report(db, ids, UUID(int=n), "2026-10-19Z", UUID(int=1))
    # A failed attempt has no report and must not erase the saved history.
    db.execute(
        "INSERT INTO github_intelligence.sync_runs"
        "(id,client_id,project_id,repository_id,window_start,window_end,status,created_at,started_at,finished_at)"
        " VALUES(%s,%s,%s,%s,'2026-10-01Z','2026-10-19Z','failed','2026-10-20Z',"
        "'2026-10-20T01:00:00Z','2026-10-20T02:00:00Z')",
        (UUID(int=2), ids["client_a"], ids["project_a"], ids["repo_a"]),
    )
    _report(db, ids, UUID(int=100), "2026-10-20Z", label="a2")
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])
    writes = []
    store = DashboardStore(
        lambda: psycopg.connect(db.info.dsn, autocommit=True, options="-c default_transaction_read_only=on"),
        scope,
    )
    app = DashboardApp(store, scope, refresh=lambda *args: writes.append(args))
    server = make_server(app, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            repo = client.get("/api/repositories").json()["repositories"][0]
            assert repo["last_status"] == "failed" and repo["report_id"] is None
            page = client.get(f"/api/reports?repository_id={ids['repo_a']}&limit=2&offset=2").json()
            assert (page["total"], page["limit"], page["offset"]) == (7, 2, 2)
            assert [row["id"] for row in page["reports"]] == [str(UUID(int=14)), str(UUID(int=13))]
            summary = client.get("/api/dashboard").json()
            assert summary["total_reports"] == summary["partial_reports"] == 7
            assert len(summary["recent_reports"]) == 5
            assert [r["id"] for r in summary["recent_reports"]] == [
                str(UUID(int=n)) for n in range(16, 11, -1)
            ]
            detail = client.get(f"/api/reports/{UUID(int=10)}/details").json()
            assert detail["content"] == "SYNTHETIC dashboard report"
            assert detail["repositories"] == [{"id": str(ids["repo_a"]), "full_name": "owner/repo-a"}]
            assert client.get(f"/api/reports/{UUID(int=100)}/details").status_code == 404
            assert client.get(f"/api/reports?repository_id={ids['repo_a2']}").json()["total"] == 0
            db.execute(
                "UPDATE github_intelligence.repositories SET status='removed' WHERE id=%s", (ids["repo_a"],),
            )
            assert client.get("/api/repositories").json()["repositories"] == []
            assert client.get("/api/reports").json()["total"] == 7
            assert client.get(f"/api/reports/{UUID(int=10)}/details").json() == detail
            assert client.get("/api/dashboard").json()["connected_repositories"] == 0
            assert writes == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
