"""Unit and regression tests for dashboard coordination and concurrency control."""
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from github_intelligence.dashboard_coordination import (
    DashboardCoordinator,
    DashboardError,
    validate_report_window,
    validate_update_payload,
)
from github_intelligence.onboarding import OnboardingError
from tests.helpers import confirmation, service
from tests.unit.test_dashboard import REPO, running

VALID_SINCE = "2026-10-01T00:00:00Z"
VALID_UNTIL = "2026-10-19T00:00:00Z"


class FakeStore:
    def __init__(self, repositories=None):
        self._repositories = repositories if repositories is not None else [
            {"id": REPO, "full_name": "owner/repo", "status": "active",
             "installation_status": "active", "last_status": "succeeded"},
        ]

    def list_repositories(self):
        return self._repositories


class FakeOnboarding:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []

    def bind(self, payload):
        self.calls.append(payload)
        if self.should_fail:
            raise RuntimeError("Synthetic onboarding failure")
        return {"status": "bound"}


def test_validate_report_window_succeeds_for_valid_payload():
    payload = {"repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL}
    repo_id, window = validate_report_window(payload)
    assert repo_id == REPO
    assert window.since == datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    assert window.until == datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)


def test_validate_report_window_rejects_non_dict_and_extra_or_missing_keys():
    for bad_payload in (
        "not-a-dict",
        [],
        None,
        {"repository_id": REPO, "since": VALID_SINCE},
        {"repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL, "extra": "field"},
        {},
    ):
        with pytest.raises(DashboardError) as exc_info:
            validate_report_window(bad_payload)
        assert exc_info.value.status == 400
        assert str(exc_info.value) == "Informe repositório e intervalo com timezone."


def test_validate_report_window_rejects_invalid_values():
    for bad_payload in (
        {"repository_id": "not-a-uuid", "since": VALID_SINCE, "until": VALID_UNTIL},
        {"repository_id": REPO, "since": "not-a-date", "until": VALID_UNTIL},
        {"repository_id": REPO, "since": VALID_SINCE, "until": "not-a-date"},
        {"repository_id": REPO, "since": VALID_UNTIL, "until": VALID_SINCE},
        {"repository_id": REPO, "since": None, "until": VALID_UNTIL},
    ):
        with pytest.raises(DashboardError) as exc_info:
            validate_report_window(bad_payload)
        assert exc_info.value.status == 400
        assert str(exc_info.value) == "Repositório ou intervalo inválido."


def test_validate_report_window_rejects_future_until():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(DashboardError) as exc_info:
        validate_report_window({"repository_id": REPO, "since": VALID_SINCE, "until": future})
    assert exc_info.value.status == 400
    assert str(exc_info.value) == "O fim do intervalo não pode estar no futuro."


def test_validate_update_payload_is_alias():
    assert validate_update_payload is validate_report_window


def test_coordinator_tracks_lock_and_releases_after_update():
    coordinator = DashboardCoordinator()
    store = FakeStore()
    executed = []

    def refresh(name, window):
        assert coordinator.locked() is True
        executed.append((name, window))

    assert coordinator.locked() is False
    coordinator.coordinate_update(store, refresh, {
        "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
    })
    assert coordinator.locked() is False
    assert len(executed) == 1
    assert executed[0][0] == "owner/repo"


def test_coordinator_releases_lock_even_when_refresh_fails():
    coordinator = DashboardCoordinator()
    store = FakeStore()

    def failing_refresh(name, window):
        assert coordinator.locked() is True
        raise RuntimeError("Synthetic refresh failure")

    with pytest.raises(RuntimeError, match="Synthetic refresh failure"):
        coordinator.coordinate_update(store, failing_refresh, {
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })

    assert coordinator.locked() is False


def test_coordinator_releases_lock_even_when_bind_fails():
    coordinator = DashboardCoordinator()
    onboarding = FakeOnboarding(should_fail=True)

    with pytest.raises(RuntimeError, match="Synthetic onboarding failure"):
        coordinator.coordinate_bind(onboarding, {"link": "owner/repo"})

    assert coordinator.locked() is False


def test_coordinator_checks_repository_status_and_pending_runs():
    coordinator = DashboardCoordinator()

    # Repository not found
    empty_store = FakeStore(repositories=[])
    with pytest.raises(DashboardError) as not_found:
        coordinator.coordinate_update(empty_store, lambda *args: None, {
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
    assert not_found.value.status == 404

    # Inactive repository
    inactive_store = FakeStore(repositories=[
        {"id": REPO, "full_name": "owner/repo", "status": "suspended",
         "installation_status": "active", "last_status": "succeeded"},
    ])
    with pytest.raises(DashboardError) as inactive:
        coordinator.coordinate_update(inactive_store, lambda *args: None, {
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
    assert inactive.value.status == 409

    # Already running collection
    running_store = FakeStore(repositories=[
        {"id": REPO, "full_name": "owner/repo", "status": "active",
         "installation_status": "active", "last_status": "running"},
    ])
    with pytest.raises(DashboardError) as in_progress:
        coordinator.coordinate_update(running_store, lambda *args: None, {
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
    assert in_progress.value.status == 409


def test_concurrency_update_during_bind_returns_409(tmp_path):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    bind_started = threading.Event()
    release_bind = threading.Event()

    original_bind = onboarding.bind

    def blocking_bind(payload):
        bind_started.set()
        if not release_bind.wait(timeout=5):
            raise TimeoutError("release_bind timed out")
        return original_bind(payload)

    onboarding.bind = blocking_bind

    with running() as (app, client, writes):
        app.onboarding = onboarding
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}

        bind_response = {}

        def run_bind():
            with httpx.Client(base_url=str(client.base_url)) as bg_client:
                bind_response["res"] = bg_client.post(
                    "/api/repositories/bind",
                    headers=headers,
                    json=confirmation("https://github.com/owner/repo"),
                )

        bind_thread = threading.Thread(target=run_bind)
        bind_thread.start()

        assert bind_started.wait(timeout=5), "Bind did not start in time"
        assert app.lock.locked() is True

        # While bind holds the lock, attempt update
        update_res = client.post("/api/update", headers=headers, json={
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
        assert update_res.status_code == 409
        assert update_res.json() == {"error": "Uma coleta já está em andamento. Aguarde."}
        assert writes == []

        # Release bind and ensure both finished cleanly
        release_bind.set()
        bind_thread.join(timeout=5)
        assert not bind_thread.is_alive()

        assert bind_response["res"].status_code == 200
        assert bind_response["res"].json()["status"] == "bound"
        assert app.lock.locked() is False


def test_concurrency_bind_during_update_returns_409(tmp_path):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    update_started = threading.Event()
    release_update = threading.Event()

    with running() as (app, client, writes):
        app.onboarding = onboarding
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}

        def blocking_refresh(repo_name, window):
            writes.append((repo_name, window))
            update_started.set()
            if not release_update.wait(timeout=5):
                raise TimeoutError("release_update timed out")

        app.refresh = blocking_refresh

        update_response = {}

        def run_update():
            with httpx.Client(base_url=str(client.base_url)) as bg_client:
                update_response["res"] = bg_client.post(
                    "/api/update",
                    headers=headers,
                    json={
                        "repository_id": REPO,
                        "since": VALID_SINCE,
                        "until": VALID_UNTIL,
                    },
                )

        update_thread = threading.Thread(target=run_update)
        update_thread.start()

        assert update_started.wait(timeout=5), "Update did not start in time"
        assert app.lock.locked() is True

        # While update holds the lock, attempt bind
        bind_res = client.post(
            "/api/repositories/bind",
            headers=headers,
            json=confirmation("https://github.com/owner/repo"),
        )
        assert bind_res.status_code == 409
        assert bind_res.json() == {"error": "Outra operação está em andamento. Aguarde."}

        # Release update and ensure both finished cleanly
        release_update.set()
        update_thread.join(timeout=5)
        assert not update_thread.is_alive()

        assert update_response["res"].status_code == 200
        assert len(writes) == 1
        assert app.lock.locked() is False


def test_concurrency_lock_released_on_update_failure():
    with running() as (app, client, writes):
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}

        def failing_refresh(repo_name, window):
            raise DashboardError(502, "A coleta falhou. Confira a conexão.")

        app.refresh = failing_refresh

        res = client.post("/api/update", headers=headers, json={
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
        assert res.status_code == 502
        assert "falhou" in res.json()["error"]
        assert app.lock.locked() is False

        # Verify subsequent update succeeds (lock was not leaked)
        app.refresh = lambda *args: writes.append(args)
        retry_res = client.post("/api/update", headers=headers, json={
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
        assert retry_res.status_code == 200
        assert len(writes) == 1


def test_concurrency_lock_released_on_bind_failure(tmp_path):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    with running() as (app, client, writes):
        app.onboarding = onboarding
        headers = {"Origin": str(client.base_url).rstrip("/"), "X-Request-Token": app.token}

        def failing_bind(payload):
            raise OnboardingError(400, "Synthetic bind failure")

        onboarding.bind = failing_bind

        res = client.post(
            "/api/repositories/bind",
            headers=headers,
            json=confirmation("https://github.com/owner/repo"),
        )
        assert res.status_code == 400
        assert res.json() == {"error": "Synthetic bind failure"}
        assert app.lock.locked() is False

        # Verify subsequent update succeeds (lock was not leaked)
        update_res = client.post("/api/update", headers=headers, json={
            "repository_id": REPO, "since": VALID_SINCE, "until": VALID_UNTIL,
        })
        assert update_res.status_code == 200
        assert len(writes) == 1
