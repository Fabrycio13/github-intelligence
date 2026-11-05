"""Dashboard reads against disposable loopback PostgreSQL, never remote settings."""

import json
from uuid import UUID

import psycopg
import pytest

from github_intelligence.storage import ProjectScope
from tests.integration.test_storage import prepared_db as _prepared_db

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


def test_repository_search_is_literal_bounded_and_includes_history_without_active_link(prepared_db):
    db, ids = prepared_db
    store = _store(db, ids)
    assert store.search_repositories(query="REPO-A") == [
        {"id": str(ids["repo_a"]), "full_name": "owner/repo-a"},
    ]
    assert store.search_repositories(query="repo-b") == []
    assert store.search_repositories(query="repo-a2") == []
    _run(db, ids, UUID(int=1), "2026-10-19Z")
    _report(db, ids, UUID(int=2), "2026-10-20Z", UUID(int=1))
    db.execute("UPDATE github_intelligence.repositories SET status='removed' WHERE id=%s", (ids["repo_a"],))
    assert store.search_repositories(query="REPO-A")[0]["id"] == str(ids["repo_a"])
    for n, name, status in (
        (100, "owner/literal_name", "inactive"),
        (101, "owner/literalXname", "active"),
        (102, "owner/removed-empty", "removed"),
    ):
        db.execute(
            "INSERT INTO github_intelligence.repositories"
            "(id,client_id,project_id,installation_id,github_repository_id,full_name,status)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (UUID(int=n), ids["client_a"], ids["project_a"], ids["install_a"], n, name, status),
        )
    assert store.search_repositories(query="literal_") == [
        {"id": str(UUID(int=100)), "full_name": "owner/literal_name"},
    ]
    assert store.search_repositories(query="%") == []
    assert store.search_repositories(query="back\\slash") == []
    assert store.search_repositories(query="removed-empty") == []
    assert store.search_repositories(query="' OR true --") == []
    db.execute(
        "INSERT INTO github_intelligence.repositories"
        "(client_id,project_id,installation_id,github_repository_id,full_name)"
        " SELECT %s,%s,%s,10000+n,'owner/bulk-' || lpad(n::text,3,'0')"
        " FROM generate_series(1,500) n",
        (ids["client_a"], ids["project_a"], ids["install_a"]),
    )
    result = store.search_repositories(query="BULK-")
    assert [row["full_name"] for row in result] == [f"owner/bulk-{n:03d}" for n in range(1, 11)]
    assert store.search_repositories(query="bulk-", limit=1) == result[:1]
    for query, limit in (("", 10), (" ", 10), ("x" * 201, 10), ("\x00", 10),
                         ("bulk", 0), ("bulk", 11), ("bulk", True), ("bulk", 1.5)):
        with pytest.raises(ValueError, match="Invalid repository search"):
            store.search_repositories(query=query, limit=limit)
    for target in ("projects", "clients"):
        key = "project_a" if target == "projects" else "client_a"
        db.execute(f"UPDATE github_intelligence.{target} SET status='inactive' WHERE id=%s", (ids[key],))
        assert store.search_repositories(query="bulk-") == []
        db.execute(f"UPDATE github_intelligence.{target} SET status='active' WHERE id=%s", (ids[key],))


def test_light_history_pages_500_reports_without_content_options_or_duplicates(prepared_db):
    db, ids = prepared_db
    _run(db, ids, UUID(int=1), "2026-10-19Z")
    db.execute(
        "INSERT INTO github_intelligence.reports"
        "(id,client_id,project_id,window_start,window_end,content,complete,generated_at)"
        " SELECT lpad(to_hex(n),32,'0')::uuid,%s,%s,'2026-10-01Z','2026-10-19Z',"
        " repeat('SYNTHETIC large content ',1000),true,'2026-10-20Z' FROM generate_series(1,500) n",
        (ids["client_a"], ids["project_a"]),
    )
    db.execute(
        "INSERT INTO github_intelligence.report_sources(client_id,project_id,report_id,sync_run_id)"
        " SELECT client_id,project_id,id,%s FROM github_intelligence.reports",
        (UUID(int=1),),
    )
    store = _store(db, ids)
    collected = []
    for offset in range(0, 500, 10):
        page = store.list_reports(limit=10, offset=offset, include_options=False)
        assert set(page) == {"reports", "total", "limit", "offset"}
        assert (page["total"], page["limit"], page["offset"]) == (500, 10, offset)
        assert len(page["reports"]) == 10
        assert all("content" not in report for report in page["reports"])
        assert len(json.dumps(page)) < 5000
        collected.extend(report["id"] for report in page["reports"])
    assert collected == [str(UUID(int=n)) for n in range(500, 0, -1)]
    assert len(set(collected)) == 500
    assert store.list_reports(limit=10, offset=500, include_options=False)["reports"] == []
    assert store.list_reports(repository_id=ids["repo_b"], include_options=False)["total"] == 0
    assert "repository_options" in store.list_reports()
    with pytest.raises(ValueError, match="Invalid repository options flag"):
        store.list_reports(include_options="false")
    assert store.get_report_details(UUID(int=500))["content"].startswith("SYNTHETIC large content ")


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



def test_get_report_executive_with_persisted_snapshots_and_project_isolation(prepared_db):
    import hashlib
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from github_intelligence.models import PullRequestSnapshot
    from github_intelligence.persistence_payload import snapshot_payload

    db, ids = prepared_db
    store_a = _store(db, ids, "a")
    store_b = _store(db, ids, "b")

    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    run_id = UUID(int=2)
    db.execute(
        "INSERT INTO github_intelligence.sync_runs"
        "(id,client_id,project_id,repository_id,window_start,window_end,status,"
        "created_at,started_at)"
        " VALUES(%s,%s,%s,%s,'2026-10-01Z','2026-10-19Z','running',%s,'2026-10-19T01:00:00Z')",
        (run_id, ids["client_a"], ids["project_a"], ids["repo_a"], "2026-10-19Z"),
    )

    pr1 = PullRequestSnapshot(
        repository="owner/repo-a",
        pull={
            "number": 1,
            "title": "Merged Feature",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-10-05T12:00:00Z",
            "user": {"login": "dev1"},
            "merged_by": {"login": "lead1"},
            "head": {"sha": "11" * 20, "ref": "feat"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/owner/repo-a/pull/1",
        },
        observed_at=now,
    )
    pr2 = PullRequestSnapshot(
        repository="owner/repo-a",
        pull={
            "number": 2,
            "title": "Open Fix",
            "state": "open",
            "created_at": "2026-10-06T10:00:00Z",
            "user": {"login": "dev2"},
            "head": {"sha": "22" * 20, "ref": "fix"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/owner/repo-a/pull/2",
        },
        observed_at=now,
    )

    for p in (pr1, pr2):
        payload = snapshot_payload(p)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        db.execute(
            "INSERT INTO github_intelligence.pr_snapshots"
            "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
            "evaluator_version,payload,fingerprint)"
            " VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),'test-v1',%s,%s)",
            (
                ids["client_a"], ids["project_a"], ids["repo_a"], run_id,
                p.pull["number"], p.pull["head"]["sha"], Jsonb(payload), digest,
            ),
        )

    db.execute(
        (
            "UPDATE github_intelligence.sync_runs "
            "SET status='succeeded', finished_at='2026-10-19T02:00:00Z' WHERE id=%s"
        ),
        (run_id,),
    )
    report_id = UUID(int=20)
    _report(db, ids, report_id, "2026-10-20Z", run_id, complete=True)

    exec_result = store_a.get_report_executive(report_id)
    assert exec_result["available"] is True
    assert exec_result["summary"]["total_prs_observed"] == 2
    assert exec_result["summary"]["merged_in_window_count"] == 1
    assert exec_result["summary"]["open_awaiting_review_count"] == 1
    assert exec_result["merged_in_window"][0]["number"] == 1
    assert exec_result["open_awaiting_review"][0]["number"] == 2
    logins = [part["login"] for part in exec_result["participants"]]
    assert "dev1" in logins
    assert "dev2" in logins
    assert "lead1" in logins

    # Project isolation: project_b cannot see project_a's executive report
    exec_b = store_b.get_report_executive(report_id)
    assert exec_b["available"] is False
    assert "não encontrado neste escopo" in exec_b["reason"]


def test_get_report_executive_case_a_complete_zero_prs(prepared_db):
    db, ids = prepared_db
    store_a = _store(db, ids, "a")
    run_id = UUID(int=101)
    db.execute(
        "INSERT INTO github_intelligence.sync_runs"
        "(id,client_id,project_id,repository_id,window_start,window_end,status,"
        "created_at,started_at,finished_at)"
        " VALUES(%s,%s,%s,%s,'2026-10-01Z','2026-10-19Z','succeeded',clock_timestamp(),"
        "clock_timestamp(),clock_timestamp())",
        (run_id, ids["client_a"], ids["project_a"], ids["repo_a"]),
    )
    report_id = UUID(int=102)
    _report(db, ids, report_id, "2026-10-20Z", run_id=run_id, complete=True)

    result = store_a.get_report_executive(report_id)
    assert result["available"] is True
    assert result["report_complete"] is True
    assert result["summary"]["total_prs_observed"] == 0
    assert result["summary"]["merged_in_window_count"] == 0
    assert result["summary"]["open_awaiting_review_count"] == 0
    assert result["summary"]["potential_blockers_count"] == 0
    assert result["merged_in_window"] == []
    assert result["open_awaiting_review"] == []
    assert result["potential_blockers"] == []
    assert result["participants"] == []
    assert any("ausência total de atividade no GitHub" in lim for lim in result["limitations"])
    assert "## 4. Participantes observados nas PRs coletadas (0)" in result["markdown_text"]


def test_get_report_executive_case_b_legacy_report_without_sources(prepared_db):
    db, ids = prepared_db
    store_a = _store(db, ids, "a")
    legacy_id = UUID(int=103)
    # Legacy report has NO entry in report_sources (run_id=None)
    _report(db, ids, legacy_id, "2026-10-20Z", run_id=None, complete=True)

    exec_result = store_a.get_report_executive(legacy_id)
    assert exec_result["available"] is False
    assert "não possui dados estruturados associados" in exec_result["reason"]

    # Technical report content must be 100% preserved
    detail = store_a.get_report_details(legacy_id)
    assert detail is not None
    assert detail["content"] == "SYNTHETIC dashboard report"


def test_get_report_executive_case_c_partial_collection_limitations(prepared_db):
    db, ids = prepared_db
    store_a = _store(db, ids, "a")
    run_id = UUID(int=104)
    _run(db, ids, run_id, "2026-10-18Z")  # status='partial'
    report_id = UUID(int=105)
    _report(db, ids, report_id, "2026-10-20Z", run_id=run_id, complete=False)

    result = store_a.get_report_executive(report_id)
    assert result["available"] is True
    assert result["report_complete"] is False
    assert any("Relatório parcial" in lim for lim in result["limitations"])


def test_get_report_executive_case_c_invalid_and_incompatible_snapshots(prepared_db):
    import hashlib

    from psycopg.types.json import Jsonb

    db, ids = prepared_db
    store_a = _store(db, ids, "a")
    run_id = UUID(int=106)
    db.execute(
        "INSERT INTO github_intelligence.sync_runs"
        "(id,client_id,project_id,repository_id,window_start,window_end,status,"
        "created_at,started_at)"
        " VALUES(%s,%s,%s,%s,'2026-10-01Z','2026-10-19Z','running',clock_timestamp(),clock_timestamp())",
        (run_id, ids["client_a"], ids["project_a"], ids["repo_a"]),
    )

    # Invalid payload: snapshot body has incompatible/corrupted schema
    bad_payload = {"schema_version": 1, "snapshot": {"corrupted_metadata": True}}
    digest = hashlib.sha256(b"bad").hexdigest()
    db.execute(
        "INSERT INTO github_intelligence.pr_snapshots"
        "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
        "evaluator_version,payload,fingerprint)"
        " VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),'test-v1',%s,%s)",
        (ids["client_a"], ids["project_a"], ids["repo_a"], run_id, 1, "ba" * 20, Jsonb(bad_payload), digest),
    )
    db.execute(
        ("UPDATE github_intelligence.sync_runs "
         "SET status='succeeded', finished_at=clock_timestamp() WHERE id=%s"),
        (run_id,),
    )

    report_id = UUID(int=107)
    _report(db, ids, report_id, "2026-10-20Z", run_id=run_id, complete=True)

    result = store_a.get_report_executive(report_id)
    assert result["available"] is False
    assert "snapshots estruturados inválidos ou incompatíveis" in result["reason"]

    # Original content preserved
    detail = store_a.get_report_details(report_id)
    assert detail is not None
    assert detail["content"] == "SYNTHETIC dashboard report"


def test_get_report_executive_does_not_silently_discard_invalid_snapshot(prepared_db):
    import hashlib
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from github_intelligence.models import PullRequestSnapshot
    from github_intelligence.persistence_payload import snapshot_payload

    db, ids = prepared_db
    store_a = _store(db, ids, "a")
    run_id = UUID(int=108)
    db.execute(
        "INSERT INTO github_intelligence.sync_runs"
        "(id,client_id,project_id,repository_id,window_start,window_end,status,"
        "created_at,started_at)"
        " VALUES(%s,%s,%s,%s,'2026-10-01Z','2026-10-19Z','running',clock_timestamp(),clock_timestamp())",
        (run_id, ids["client_a"], ids["project_a"], ids["repo_a"]),
    )

    # 1 valid snapshot
    pr_valid = PullRequestSnapshot(
        repository="owner/repo-a",
        pull={
            "number": 1,
            "title": "Good PR",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-10-05T12:00:00Z",
            "user": {"login": "dev1"},
            "merged_by": {"login": "lead1"},
            "head": {"sha": "11" * 20, "ref": "feat"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/owner/repo-a/pull/1",
        },
        observed_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
    )
    good_payload = snapshot_payload(pr_valid)
    d1 = hashlib.sha256(b"good").hexdigest()
    db.execute(
        "INSERT INTO github_intelligence.pr_snapshots"
        "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
        "evaluator_version,payload,fingerprint)"
        " VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),'test-v1',%s,%s)",
        (ids["client_a"], ids["project_a"], ids["repo_a"], run_id, 1, "11" * 20, Jsonb(good_payload), d1),
    )

    # 1 corrupted snapshot that passes DB jsonb check but fails Pydantic/contract validation
    bad_payload = {"schema_version": 1, "snapshot": {"pull": "not a dictionary"}}
    d2 = hashlib.sha256(b"bad2").hexdigest()
    db.execute(
        "INSERT INTO github_intelligence.pr_snapshots"
        "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
        "evaluator_version,payload,fingerprint)"
        " VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),'test-v1',%s,%s)",
        (ids["client_a"], ids["project_a"], ids["repo_a"], run_id, 2, "be" * 20, Jsonb(bad_payload), d2),
    )
    db.execute(
        ("UPDATE github_intelligence.sync_runs "
         "SET status='succeeded', finished_at=clock_timestamp() WHERE id=%s"),
        (run_id,),
    )

    report_id = UUID(int=109)
    _report(db, ids, report_id, "2026-10-20Z", run_id=run_id, complete=True)

    # Must NOT return available: True with total_prs_observed=1
    result = store_a.get_report_executive(report_id)
    assert result["available"] is False
    assert "snapshots estruturados inválidos ou incompatíveis" in result["reason"]
