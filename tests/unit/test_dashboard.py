"""Public localhost HTTP seam; no remote network or credentials."""
import json
from contextlib import contextmanager
from threading import Thread
from uuid import UUID

import httpx

from github_intelligence.storage import ProjectScope

REPO = "00000000-0000-0000-0000-000000000001"
REPORT = "00000000-0000-0000-0000-000000000002"


class Store:
    def search_repositories(self, *, query, limit=10):
        return [{"id": REPO, "full_name": "owner/repo"}][:limit] if query == "RePo" else []

    def list_reports(self, *, repository_id=None, limit=20, offset=0, include_options=True):
        result = {"reports": [], "total": 0, "limit": limit, "offset": offset,
                  "filter": str(repository_id) if repository_id else None}
        if include_options:
            result["repository_options"] = []
        return result

    def get_dashboard(self):
        return {"connected_repositories": 1, "total_reports": 0}

    def get_report_details(self, report_id):
        report = self.get_report(report_id)
        return {**report, "repositories": [], "window_start": None, "window_end": None} if report else None

    def list_repositories(self):
        return [{"id": REPO, "full_name": "owner/repo", "status": "active",
                 "installation_status": "active", "last_status": "succeeded", "report_id": REPORT}]

    def get_report(self, report_id):
        if str(report_id) == REPORT:
            return {"id": REPORT, "content": "# SYNTHETIC <script>not executable</script>", "complete": True}
        return None


@contextmanager
def running():
    from github_intelligence.dashboard import DashboardApp, make_server

    writes = []
    scope = ProjectScope(client_id=UUID(REPO), project_id=UUID(REPORT))
    app = DashboardApp(Store(), scope, refresh=lambda *args: writes.append(args))
    server = make_server(app, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            yield app, client, writes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_lists_and_opens_report_without_triggering_collection():
    with running() as (_, client, writes):
        response = client.get("/")
        assert response.status_code == 200
        assert "GitHub Intelligence" in response.text
        assert "Content-Security-Policy" in response.headers
        listing = client.get("/api/repositories").json()
        assert listing["repositories"][0]["full_name"] == "owner/repo"
        report = client.get(f"/api/reports/{REPORT}").json()
        assert "SYNTHETIC" in report["content"]
        assert client.get("/api/reports/not-a-uuid").status_code == 400
        assert client.get("/api/reports/00000000-0000-0000-0000-000000000099").status_code == 404
        assert writes == []


def test_browser_favicon_request_does_not_fail_or_trigger_collection():
    with running() as (_, client, writes):
        response = client.get("/favicon.ico")
        assert response.status_code == 204
        assert response.content == b""
        assert writes == []


def test_repository_search_is_bounded_strict_and_read_only():
    with running() as (_, client, writes):
        response = client.get("/api/repositories/search", params={"q": "RePo", "limit": 1})
        assert response.status_code == 200
        assert response.json() == {"repositories": [{"id": REPO, "full_name": "owner/repo"}]}
        assert client.get("/api/repositories/search?q=missing").json() == {"repositories": []}
        for query in (
            "", "q=", "q=%20%20", "q=x&limit=0", "q=x&limit=11", "q=x&limit=-1",
            "q=x&limit=1.0", "q=x&limit=", "q=x&limit=１０", "q=x&unknown=1",
            "q=x&q=y", "q=x&limit=1&limit=2", "q=%00", "q=" + "x" * 201,
        ):
            assert client.get("/api/repositories/search?" + query).status_code == 400, query
        assert client.get("/api/repositories/search", params={"q": "x" * 200}).status_code == 200
        assert client.get("/api/repositories/search?q=x", headers={"Host": "evil.test"}).status_code == 403
        assert writes == []


def test_read_only_history_summary_and_details_routes_preserve_original_report():
    with running() as (_, client, writes):
        assert client.get("/api/reports").json() == {
            "reports": [], "repository_options": [], "total": 0, "limit": 20, "offset": 0, "filter": None,
        }
        assert client.get(f"/api/reports?repository_id={REPO}&limit=5&offset=10").json() == {
            "reports": [], "repository_options": [], "total": 0, "limit": 5, "offset": 10, "filter": REPO,
        }
        assert client.get("/api/dashboard").json() == {"connected_repositories": 1, "total_reports": 0}
        assert client.get(f"/api/reports/{REPORT}/details").json()["repositories"] == []
        assert client.get(f"/api/reports/{REPORT}").json() == Store().get_report(UUID(REPORT))
        assert client.get(f"/api/reports/{REPO}/details").status_code == 404
        for path in (
            "/api/reports?limit=0", "/api/reports?limit=101", "/api/reports?offset=-1",
            "/api/reports?offset=1000001", "/api/reports?limit=x", "/api/reports?limit=",
            "/api/reports?repository_id=bad", "/api/reports?repository_id=",
            "/api/reports?limit=2&limit=3", "/api/reports?unknown=1",
            "/api/reports?repository_id=" + REPO + "&repository_id=" + REPO,
            "/api/dashboard?limit=1", f"/api/reports/{REPORT}/details?x=1",
            f"/api/reports/{REPORT}?x=1", "/api/reports/not-a-uuid/details",
        ):
            assert client.get(path).status_code == 400, path
        assert client.get("/api/dashboard", headers={"Host": "evil.test"}).status_code == 403
        assert writes == []


def test_history_can_omit_options_without_changing_pagination_or_legacy_defaults():
    with running() as (_, client, writes):
        page = client.get(f"/api/reports?repository_id={REPO}&limit=10&offset=10&include_options=false")
        assert page.status_code == 200
        assert page.json() == {"reports": [], "total": 0, "limit": 10, "offset": 10, "filter": REPO}
        assert "repository_options" in client.get("/api/reports?include_options=true").json()
        for value in ("", "False", "0", "1", "yes", "true&include_options=false"):
            assert client.get("/api/reports?include_options=" + value).status_code == 400
        assert writes == []


def test_read_only_endpoints_report_storage_failure_instead_of_zero(monkeypatch):
    from github_intelligence.storage import StorageError

    def unavailable(*args, **kwargs):
        raise StorageError("sensitive driver diagnostic")

    with running() as (app, client, writes):
        for method in ("list_reports", "get_dashboard", "get_report_details", "search_repositories"):
            monkeypatch.setattr(app.store, method, unavailable)
        for path in (
            "/api/reports", "/api/dashboard", f"/api/reports/{REPORT}/details",
            "/api/repositories/search?q=repo",
        ):
            response = client.get(path)
            assert response.status_code == 503
            assert set(response.json()) == {"error"}
            assert "sensitive" not in response.text
            assert "Content-Security-Policy" in response.headers
        assert writes == []


def test_mutation_requires_same_origin_token_and_known_active_repository():
    with running() as (app, client, writes):
        payload = {"repository_id": REPO, "since": "2026-10-01T00:00:00Z",
                   "until": "2026-10-19T00:00:00Z"}
        assert client.post("/api/update", json=payload).status_code == 403
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
        foreign = client.post("/api/update", json=payload, headers={**headers, "Origin": "https://evil.test"})
        assert foreign.status_code == 403
        unknown = client.post("/api/update", json={**payload, "repository_id": REPORT}, headers=headers)
        assert unknown.status_code == 404
        assert writes == []
        response = client.post("/api/update", json=payload, headers=headers)
        assert response.status_code == 200
        assert len(writes) == 1
        assert writes[0][0] == "owner/repo"


def test_dashboard_rejects_dns_rebinding_and_invalid_payloads():
    with running() as (app, client, writes):
        assert client.get("/api/repositories", headers={"Host": "evil.test"}).status_code == 403
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token,
                   "Content-Type": "application/json"}
        assert client.post("/api/update", content="{", headers=headers).status_code == 400
        payload = {"repository_id": REPO, "since": "nope", "until": "nope"}
        assert client.post("/api/update", content=json.dumps(payload), headers=headers).status_code == 400
        assert writes == []


def test_existing_running_sync_blocks_another_update():
    with running() as (app, client, writes):
        original = app.store.list_repositories

        def active_run():
            return [{**repo, "last_status": "running"} for repo in original()]

        app.store.list_repositories = active_run
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
        response = client.post("/api/update", headers=headers, json={
            "repository_id": REPO, "since": "2026-10-01T00:00:00Z", "until": "2026-10-19T00:00:00Z",
        })
        assert response.status_code == 409
        assert writes == []


def test_refresh_uses_the_public_cli_with_fixed_scope_and_no_shell(tmp_path, monkeypatch):
    import subprocess
    from unittest.mock import Mock

    from github_intelligence.dashboard import cli_refresh
    from github_intelligence.models import ReportWindow

    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(subprocess, "run", runner)
    scope = ProjectScope(client_id=UUID(REPO), project_id=UUID(REPORT))
    cli_refresh(tmp_path, scope, "personal", "pilot")(
        "owner/repo", ReportWindow(since="2026-10-01T00:00:00Z", until="2026-10-19T00:00:00Z"),
    )
    args = runner.call_args.args[0]
    assert args[1:3] == ["-m", "github_intelligence"]
    assert args[args.index("--repo") + 1] == "owner/repo"
    assert args[args.index("--client-id") + 1] == REPO
    assert args[args.index("--project-id") + 1] == REPORT
    assert args[-1] == "--apply"
    assert runner.call_args.kwargs.get("shell", False) is False
