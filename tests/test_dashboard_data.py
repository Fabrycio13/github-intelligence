"""Dashboard reads against disposable loopback PostgreSQL, never remote settings."""

import json
from uuid import UUID

import psycopg
import pytest
from test_storage import prepared_db as _prepared_db

from github_intelligence.storage import ProjectScope

prepared_db = _prepared_db


def test_listing_is_scoped_and_json_safe_without_history(prepared_db):
    from github_intelligence.dashboard_data import DashboardStore

    db, ids = prepared_db
    opened = []

    def connect():
        connection = psycopg.connect(
            db.info.dsn, autocommit=True, options="-c default_transaction_read_only=on"
        )
        opened.append(connection)
        return connection

    store = DashboardStore(connect, ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"]))
    expected = [
        {
            "id": str(ids["repo_a"]),
            "full_name": "owner/repo-a",
            "status": "active",
            "installation_status": "active",
            "last_status": None,
            "last_started_at": None,
            "last_finished_at": None,
            "window_start": None,
            "window_end": None,
            "report_id": None,
            "report_complete": None,
        }
    ]
    assert store.list_repositories() == expected
    assert json.loads(json.dumps(store.list_repositories())) == expected
    assert len(opened) == 2
    assert opened[0] is not opened[1]
    assert all(connection.closed for connection in opened)
    assert isinstance(ids["repo_a"], UUID)


def _store(db, ids, label="a"):
    from github_intelligence.dashboard_data import DashboardStore

    client = "a" if label == "a2" else label
    return DashboardStore(
        lambda: psycopg.connect(db.info.dsn, autocommit=True, options="-c default_transaction_read_only=on"),
        ProjectScope(client_id=ids[f"client_{client}"], project_id=ids[f"project_{label}"]),
    )


def _run(db, ids, run_id, created_at, label="a"):
    client = "a" if label == "a2" else label
    db.execute(
        "INSERT INTO github_intelligence.sync_runs"
        "(id,client_id,project_id,repository_id,window_start,window_end,status,"
        "created_at,started_at,finished_at)"
        " VALUES(%s,%s,%s,%s,'2026-10-01Z','2026-10-19Z','partial',%s,"
        "'2026-10-19T01:00:00Z','2026-10-19T02:00:00Z')",
        (run_id, ids[f"client_{client}"], ids[f"project_{label}"], ids[f"repo_{label}"], created_at),
    )


def _report(db, ids, report_id, generated_at, run_id=None, label="a"):
    client = "a" if label == "a2" else label
    db.execute(
        "INSERT INTO github_intelligence.reports"
        "(id,client_id,project_id,window_start,window_end,content,complete,generated_at)"
        " VALUES(%s,%s,%s,'2026-10-01Z','2026-10-19Z','SYNTHETIC dashboard report',false,%s)",
        (report_id, ids[f"client_{client}"], ids[f"project_{label}"], generated_at),
    )
    if run_id is not None:
        db.execute(
            "INSERT INTO github_intelligence.report_sources(client_id,project_id,report_id,sync_run_id)"
            " VALUES(%s,%s,%s,%s)",
            (ids[f"client_{client}"], ids[f"project_{label}"], report_id, run_id),
        )


def test_listing_selects_latest_run_and_only_its_latest_sourced_report(prepared_db):
    db, ids = prepared_db
    old_run, lower_run, latest_run = (UUID(int=n) for n in (100, 1, 2))
    _run(db, ids, old_run, "2026-10-18Z")
    # Insert the larger ID first, proving ties are not insertion order.
    _run(db, ids, latest_run, "2026-10-19Z")
    _run(db, ids, lower_run, "2026-10-19Z")
    latest_report = UUID(int=20)
    _report(db, ids, latest_report, "2026-10-20Z", latest_run)
    _report(db, ids, UUID(int=10), "2026-10-20Z", latest_run)
    _report(db, ids, UUID(int=30), "2026-10-19Z", latest_run)
    _report(db, ids, UUID(int=40), "2026-10-21Z", old_run)
    _report(db, ids, UUID(int=50), "2026-10-22Z")
    # A newer report sourced from another repository in the same project is also excluded.
    from uuid import uuid4

    other_repo = uuid4()
    db.execute(
        "INSERT INTO github_intelligence.repositories"
        "(id,client_id,project_id,installation_id,github_repository_id,full_name)"
        " VALUES(%s,%s,%s,%s,9000,'owner/other')",
        (other_repo, ids["client_a"], ids["project_a"], ids["install_a"]),
    )
    other_ids = {**ids, "repo_a": other_repo}
    other_run = uuid4()
    _run(db, other_ids, other_run, "2026-10-19Z")
    _report(db, ids, UUID(int=60), "2026-10-23Z", other_run)
    result = next(row for row in _store(db, ids).list_repositories() if row["id"] == str(ids["repo_a"]))
    assert result == {
        "id": str(ids["repo_a"]),
        "full_name": "owner/repo-a",
        "status": "active",
        "installation_status": "active",
        "last_status": "partial",
        "last_started_at": "2026-10-19T01:00:00+00:00",
        "last_finished_at": "2026-10-19T02:00:00+00:00",
        "window_start": "2026-10-01T00:00:00+00:00",
        "window_end": "2026-10-19T00:00:00+00:00",
        "report_id": str(latest_report),
        "report_complete": False,
    }
    json.dumps(result)


def test_latest_run_without_source_does_not_reuse_previous_report(prepared_db):
    db, ids = prepared_db
    old_run, new_run = UUID(int=1), UUID(int=2)
    _run(db, ids, old_run, "2026-10-18Z")
    _report(db, ids, UUID(int=3), "2026-10-19Z", old_run)
    _run(db, ids, new_run, "2026-10-19Z")
    result = _store(db, ids).list_repositories()[0]
    assert result["last_status"] == "partial"
    assert result["report_id"] is result["report_complete"] is None


@pytest.mark.parametrize("target", ["client", "project", "removed"])
def test_listing_hides_inactive_scope_and_removed_repository(prepared_db, target):
    db, ids = prepared_db
    if target == "client":
        db.execute("UPDATE github_intelligence.clients SET status='inactive' WHERE id=%s", (ids["client_a"],))
    elif target == "project":
        db.execute(
            "UPDATE github_intelligence.projects SET status='inactive' WHERE id=%s", (ids["project_a"],)
        )
    else:
        db.execute(
            "UPDATE github_intelligence.repositories SET status='removed' WHERE id=%s", (ids["repo_a"],)
        )
    assert _store(db, ids).list_repositories() == []


def test_listing_includes_inactive_repository_and_suspended_installation(prepared_db):
    db, ids = prepared_db
    db.execute("UPDATE github_intelligence.repositories SET status='inactive' WHERE id=%s", (ids["repo_a"],))
    db.execute(
        "UPDATE github_intelligence.github_installations SET status='suspended' WHERE id=%s",
        (ids["install_a"],),
    )
    result = _store(db, ids).list_repositories()[0]
    assert result["status"] == "inactive"
    assert result["installation_status"] == "suspended"


def test_get_report_returns_json_safe_content_only_for_its_active_scope(prepared_db):
    db, ids = prepared_db
    report_id = UUID(int=100)
    _report(db, ids, report_id, "2026-10-20Z")
    result = _store(db, ids).get_report(report_id)
    assert result == {
        "id": str(report_id),
        "content": "SYNTHETIC dashboard report",
        "complete": False,
        "generated_at": "2026-10-20T00:00:00+00:00",
    }
    json.dumps(result)
    assert _store(db, ids).get_report(UUID(int=999)) is None
    assert _store(db, ids, "a2").get_report(report_id) is None
    assert _store(db, ids, "b").get_report(report_id) is None
    db.execute("UPDATE github_intelligence.projects SET status='inactive' WHERE id=%s", (ids["project_a"],))
    assert _store(db, ids).get_report(report_id) is None
    db.execute("UPDATE github_intelligence.projects SET status='active' WHERE id=%s", (ids["project_a"],))
    db.execute("UPDATE github_intelligence.clients SET status='inactive' WHERE id=%s", (ids["client_a"],))
    assert _store(db, ids).get_report(report_id) is None
