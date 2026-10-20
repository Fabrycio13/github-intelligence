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


def _report(db, ids, report_id, generated_at, run_id=None, label="a", complete=False):
    client = "a" if label == "a2" else label
    db.execute(
        "INSERT INTO github_intelligence.reports"
        "(id,client_id,project_id,window_start,window_end,content,complete,generated_at)"
        " VALUES(%s,%s,%s,'2026-10-01Z','2026-10-19Z','SYNTHETIC dashboard report',%s,%s)",
        (report_id, ids[f"client_{client}"], ids[f"project_{label}"], complete, generated_at),
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


def test_history_paginates_distinct_reports_and_keeps_removed_sources(prepared_db):
    db, ids = prepared_db
    _run(db, ids, UUID(int=1), "2026-10-18Z")
    _run(db, ids, UUID(int=2), "2026-10-19Z")
    _report(db, ids, UUID(int=10), "2026-10-20Z", UUID(int=1))
    _report(db, ids, UUID(int=20), "2026-10-20Z", UUID(int=2))
    _report(db, ids, UUID(int=30), "2026-10-19Z")
    _report(db, ids, UUID(int=40), "2026-10-21Z", label="b")
    db.execute(
        "INSERT INTO github_intelligence.report_sources(client_id,project_id,report_id,sync_run_id)"
        " VALUES(%s,%s,%s,%s)",
        (ids["client_a"], ids["project_a"], UUID(int=20), UUID(int=1)),
    )
    db.execute("UPDATE github_intelligence.repositories SET status='removed' WHERE id=%s", (ids["repo_a"],))
    store = _store(db, ids)
    page = store.list_reports(limit=1, offset=1)
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    assert page["reports"] == [{
        "id": str(UUID(int=10)), "complete": False, "generated_at": "2026-10-20T00:00:00+00:00",
        "window_start": "2026-10-01T00:00:00+00:00", "window_end": "2026-10-19T00:00:00+00:00",
        "repositories": [{"id": str(ids["repo_a"]), "full_name": "owner/repo-a"}],
    }]
    filtered = store.list_reports(repository_id=ids["repo_a"])
    assert [row["id"] for row in filtered["reports"]] == [str(UUID(int=20)), str(UUID(int=10))]
    assert filtered["total"] == 2
    assert store.list_reports(offset=99)["total"] == 3
    assert store.list_reports(offset=99)["reports"] == []
    assert store.list_reports()["reports"][-1]["repositories"] == []
    assert store.list_reports(repository_id=ids["repo_b"])["total"] == 0


def test_report_details_keep_all_repositories_and_deny_other_scopes(prepared_db):
    db, ids = prepared_db
    _run(db, ids, UUID(int=1), "2026-10-18Z")
    _report(db, ids, UUID(int=10), "2026-10-20Z", UUID(int=1))
    other = UUID(int=90)
    db.execute(
        "INSERT INTO github_intelligence.repositories"
        "(id,client_id,project_id,installation_id,github_repository_id,full_name)"
        " VALUES(%s,%s,%s,%s,9000,'owner/other')",
        (other, ids["client_a"], ids["project_a"], ids["install_a"]),
    )
    _run(db, {**ids, "repo_a": other}, UUID(int=2), "2026-10-19Z")
    db.execute(
        "INSERT INTO github_intelligence.report_sources(client_id,project_id,report_id,sync_run_id)"
        " VALUES(%s,%s,%s,%s)",
        (ids["client_a"], ids["project_a"], UUID(int=10), UUID(int=2)),
    )
    store = _store(db, ids)
    detail = store.get_report_details(UUID(int=10))
    assert detail == {**store.list_reports()["reports"][0], "content": "SYNTHETIC dashboard report"}
    assert detail["repositories"] == [
        {"id": str(other), "full_name": "owner/other"},
        {"id": str(ids["repo_a"]), "full_name": "owner/repo-a"},
    ]
    assert store.get_dashboard()["repository_reports"] == [
        {"id": str(other), "full_name": "owner/other", "count": 1},
        {"id": str(ids["repo_a"]), "full_name": "owner/repo-a", "count": 1},
    ]
    assert store.get_dashboard()["total_reports"] == 1
    assert store.list_reports(repository_id=other)["reports"][0]["repositories"] == detail["repositories"]
    assert store.get_report_details(UUID(int=999)) is None
    for label in ("a2", "b"):
        assert _store(db, ids, label).get_report_details(UUID(int=10)) is None
        assert _store(db, ids, label).list_reports()["total"] == 0
    db.execute("UPDATE github_intelligence.projects SET status='inactive' WHERE id=%s", (ids["project_a"],))
    assert store.get_report_details(UUID(int=10)) is None
    assert store.list_reports()["total"] == 0


def test_dashboard_summary_counts_distinct_reports_and_utc_days(prepared_db):
    from datetime import datetime, timedelta, timezone

    db, ids = prepared_db
    today = datetime.now(timezone.utc).date()
    _run(db, ids, UUID(int=1), "2026-10-18Z")
    _run(db, ids, UUID(int=2), "2026-10-19Z")
    _report(db, ids, UUID(int=10), f"{today}T00:00:00Z", UUID(int=1))
    _report(db, ids, UUID(int=20), f"{today - timedelta(days=29)}T23:59:59Z", UUID(int=2))
    _report(db, ids, UUID(int=30), f"{today - timedelta(days=30)}T23:59:59Z", complete=True)
    _report(db, ids, UUID(int=40), f"{today}T00:00:00Z", label="b")

    db.execute(
        "INSERT INTO github_intelligence.report_sources(client_id,project_id,report_id,sync_run_id)"
        " VALUES(%s,%s,%s,%s)",
        (ids["client_a"], ids["project_a"], UUID(int=10), UUID(int=2)),
    )
    store = _store(db, ids)
    summary = store.get_dashboard()
    assert set(summary) == {"connected_repositories", "total_reports", "partial_reports", "daily_reports",
                            "repository_reports", "recent_reports", "period_start", "period_end"}
    assert (summary["connected_repositories"], summary["total_reports"], summary["partial_reports"]) == (
        1, 3, 2,
    )
    assert summary["period_start"] == str(today - timedelta(days=29))
    assert summary["period_end"] == str(today)
    assert summary["daily_reports"] == [
        {"date": str(today - timedelta(days=29 - n)), "count": 1 if n in (0, 29) else 0}
        for n in range(30)
    ]
    assert summary["repository_reports"] == [
        {"id": str(ids["repo_a"]), "full_name": "owner/repo-a", "count": 2},
    ]
    assert summary["recent_reports"] == store.list_reports()["reports"]
    db.execute("UPDATE github_intelligence.repositories SET status='removed' WHERE id=%s", (ids["repo_a"],))
    removed = store.get_dashboard()
    assert removed["connected_repositories"] == 0
    assert removed["repository_reports"] == summary["repository_reports"]
    assert removed["total_reports"] == 3
    db.execute("UPDATE github_intelligence.clients SET status='inactive' WHERE id=%s", (ids["client_a"],))
    hidden = store.get_dashboard()
    assert hidden["connected_repositories"] == hidden["total_reports"] == hidden["partial_reports"] == 0
    assert hidden["recent_reports"] == hidden["repository_reports"] == []
    assert sum(day["count"] for day in hidden["daily_reports"]) == 0


@pytest.mark.parametrize("target", ["repository", "installation", "project", "client"])
def test_dashboard_connected_count_requires_every_active_link(prepared_db, target):
    db, ids = prepared_db
    store = _store(db, ids)
    assert store.get_dashboard()["connected_repositories"] == 1
    table, key, status = {
        "repository": ("repositories", "repo_a", "inactive"),
        "installation": ("github_installations", "install_a", "suspended"),
        "project": ("projects", "project_a", "inactive"),
        "client": ("clients", "client_a", "inactive"),
    }[target]
    from psycopg import sql

    db.execute(
        sql.SQL("UPDATE github_intelligence.{} SET status=%s WHERE id=%s").format(sql.Identifier(table)),
        (status, ids[key]),
    )
    assert store.get_dashboard()["connected_repositories"] == 0
    assert _store(db, ids, "a2").get_dashboard()["total_reports"] == 0


def test_history_repository_options_include_removed_repository_outside_filtered_page(prepared_db):
    db, ids = prepared_db
    other = UUID(int=90)
    db.execute(
        "INSERT INTO github_intelligence.repositories"
        "(id,client_id,project_id,installation_id,github_repository_id,full_name,status)"
        " VALUES(%s,%s,%s,%s,9000,'owner/removed','removed')",
        (other, ids["client_a"], ids["project_a"], ids["install_a"]),
    )
    _run(db, ids, UUID(int=1), "2026-10-19Z")
    _report(db, ids, UUID(int=10), "2026-10-20Z", UUID(int=1))
    _run(db, {**ids, "repo_a": other}, UUID(int=2), "2026-10-18Z")
    _report(db, ids, UUID(int=20), "2026-10-19Z", UUID(int=2))
    _run(db, ids, UUID(int=3), "2026-10-19Z", label="b")
    _report(db, ids, UUID(int=30), "2026-10-20Z", UUID(int=3), label="b")
    store = _store(db, ids)
    page = store.list_reports(repository_id=ids["repo_a"], limit=1)
    assert [row["id"] for row in page["reports"]] == [str(UUID(int=10))]
    expected = [
        {"id": str(other), "full_name": "owner/removed"},
        {"id": str(ids["repo_a"]), "full_name": "owner/repo-a"},
    ]
    assert page["repository_options"] == expected
    assert store.list_reports(offset=100)["repository_options"] == expected
    assert _store(db, ids, "a2").list_reports()["repository_options"] == []
    db.execute("UPDATE github_intelligence.projects SET status='inactive' WHERE id=%s", (ids["project_a"],))
    assert store.list_reports()["repository_options"] == []
