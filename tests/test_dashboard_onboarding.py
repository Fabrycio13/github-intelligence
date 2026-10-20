"""Onboarding endpoints preserve explicit confirm and localhost write protection."""
from test_dashboard import running
from test_onboarding import confirmation, service


def test_http_inspection_is_read_only_and_binding_requires_explicit_request(tmp_path):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    with running() as (app, client, collections):
        app.onboarding = onboarding
        payload = {"link": "https://github.com/owner/repo"}
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
        assert client.post("/api/repositories/bind", json=payload).status_code == 403
        inspected = client.post("/api/repositories/inspect", json=payload, headers=headers)
        assert inspected.status_code == 200
        assert inspected.json()["status"] == "ready"
        assert binder.writes == []
        assert registry.entries() == []
        bound = client.post(
            "/api/repositories/bind", json=confirmation("https://github.com/owner/repo"), headers=headers,
        )
        assert bound.status_code == 200
        assert bound.json()["status"] == "bound"
        assert len(binder.writes) == 1
        assert len(access.checked) == 2
        assert collections == []


def test_browser_cannot_supply_forged_github_or_project_ids(tmp_path):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    with running() as (app, client, collections):
        app.onboarding = onboarding
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
        response = client.post("/api/repositories/bind", headers=headers, json={
            "link": "owner/repo", "installation_id": 999, "project_id": "forged",
        })
        assert response.status_code == 400
        assert binder.writes == []
        assert access.checked == []
        assert collections == []
        assert registry.entries() == []


def test_invalid_repository_url_returns_400_instead_of_closing_http_connection(tmp_path, monkeypatch):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")

    def reject(link):
        raise ValueError("synthetic invalid URL")

    monkeypatch.setattr(access, "inspect", reject)
    with running() as (app, client, collections):
        app.onboarding = onboarding
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
        response = client.post("/api/repositories/inspect", headers=headers, json={
            "link": "https://evil.test/a/b",
        })
        assert response.status_code == 400
        assert binder.writes == []
        assert collections == []


def test_http_confirmation_rejects_replaced_repository_without_writing(tmp_path, monkeypatch):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    with running() as (app, client, collections):
        app.onboarding = onboarding
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}
        inspected = client.post(
            "/api/repositories/inspect", headers=headers, json={"link": "owner/repo"},
        ).json()
        assert inspected["github_id"] == 10
        replacement = {**inspected, "github_id": 11}
        monkeypatch.setattr(access, "inspect", lambda link: replacement)
        refused = client.post("/api/repositories/bind", headers=headers, json=confirmation())
        assert refused.status_code == 409
        assert "mudou" in refused.json()["error"]
        assert binder.writes == []
        assert registry.entries() == []
        assert collections == []
