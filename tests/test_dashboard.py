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
