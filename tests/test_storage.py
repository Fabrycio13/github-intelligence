"""Integration against a disposable LOCAL PostgreSQL, never the Supabase target."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

SCHEMA = "github_intelligence"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0001_initial.sql"
TABLES = {
    "clients",
    "projects",
    "project_memberships",
    "github_installations",
    "repositories",
    "sync_runs",
    "pr_snapshots",
    "reports",
    "report_sources",
    "audit_events",
}


@pytest.fixture
def prepared_db(pg_admin):
    assert pg_admin.info.host in {"127.0.0.1", "localhost"}
    assert pg_admin.info.dbname.startswith("ghi_test_")
    # Test-only stand-in for Supabase's managed auth schema; migration itself must never create it.
    pg_admin.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
    pg_admin.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
    ids = {
        name: uuid4()
        for name in [
            "client_a",
            "client_b",
            "project_a",
            "project_b",
            "project_a2",
            "repo_a",
            "repo_b",
            "repo_a2",
            "install_a",
            "install_b",
        ]
    }
    for label in ("a", "b"):
        pg_admin.execute(
            f"INSERT INTO {SCHEMA}.clients(id,name) VALUES (%s,%s)",
            (ids[f"client_{label}"], f"SYNTHETIC client {label}"),
        )
        pg_admin.execute(
            f"INSERT INTO {SCHEMA}.github_installations"
            "(id,client_id,github_app_id,github_installation_id) VALUES (%s,%s,1,1)",
            (ids[f"install_{label}"], ids[f"client_{label}"]),
        )
    for label, client, repo_number in [("a", "a", 1001), ("b", "b", 1002), ("a2", "a", 1003)]:
        pg_admin.execute(
            f"INSERT INTO {SCHEMA}.projects(id,client_id,name) VALUES (%s,%s,%s)",
            (ids[f"project_{label}"], ids[f"client_{client}"], f"SYNTHETIC {label}"),
        )
        pg_admin.execute(
            f"INSERT INTO {SCHEMA}.repositories"
            "(id,client_id,project_id,installation_id,github_repository_id,full_name)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (
                ids[f"repo_{label}"],
                ids[f"client_{client}"],
                ids[f"project_{label}"],
                ids[f"install_{client}"],
                repo_number,
                f"owner/repo-{label}",
            ),
        )
    return pg_admin, ids


def set_scope(connection, ids, label="a"):
    client = "a" if label == "a2" else label
    connection.execute("SET LOCAL ROLE github_intelligence_runtime")
    connection.execute(
        "SELECT set_config('ghi.client_id',%s,true),set_config('ghi.project_id',%s,true)",
        (str(ids[f"client_{client}"]), str(ids[f"project_{label}"])),
    )


def test_migration_creates_private_rls_tables_and_restricted_owners(prepared_db):
    db, _ = prepared_db
    rows = db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (SCHEMA,)).fetchall()
    assert {row[0] for row in rows} == TABLES
    rows = db.execute(
        "SELECT c.relrowsecurity,c.relforcerowsecurity,r.rolname FROM pg_class c"
        " JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner"
        " WHERE n.nspname=%s AND c.relkind='r'",
        (SCHEMA,),
    ).fetchall()
    assert all(enabled and forced and owner == "ghi_owner" for enabled, forced, owner in rows)
    flags = db.execute(
        "SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles"
        " WHERE rolname='github_intelligence_runtime'"
    ).fetchone()
    assert flags == (False, False, False, False, False)


def test_runtime_without_context_sees_nothing_and_context_does_not_leak(prepared_db):
    db, ids = prepared_db
    with db.transaction():
        db.execute("SET LOCAL ROLE github_intelligence_runtime")
        assert db.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == []
    with db.transaction():
        set_scope(db, ids)
        assert db.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == [(ids["repo_a"],)]
    with db.transaction():
        db.execute("SET LOCAL ROLE github_intelligence_runtime")
        assert db.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == []
    with db.transaction():
        set_scope(db, ids, "a2")
        assert db.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == [(ids["repo_a2"],)]


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_browser_and_service_roles_have_no_schema_access(prepared_db, role):
    db, _ = prepared_db
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
        # Role is from the fixed test parameter list, never user input.
        db.execute(psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(role)))
        db.execute(f"SELECT * FROM {SCHEMA}.repositories")


def test_composite_fk_rejects_cross_client_reference_even_for_migration_admin(prepared_db):
    db, ids = prepared_db
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            f"INSERT INTO {SCHEMA}.repositories"
            "(client_id,project_id,installation_id,github_repository_id,full_name)"
            " VALUES(%s,%s,%s,1009,'owner/wrong')",
            (ids["client_a"], ids["project_b"], ids["install_a"]),
        )


def test_runtime_cannot_create_objects_or_delete_selection(prepared_db):
    db, ids = prepared_db
    for query in [f"CREATE TABLE {SCHEMA}.forbidden(id int)", f"DELETE FROM {SCHEMA}.repositories"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            set_scope(db, ids)
            db.execute(query)


def _running(db, ids, label="a"):
    client = "a" if label == "a2" else label
    return db.execute(
        f"INSERT INTO {SCHEMA}.sync_runs"
        "(client_id,project_id,repository_id,window_start,window_end,status,started_at)"
        " VALUES(%s,%s,%s,'2026-10-01T00:00:00Z','2026-10-19T00:00:00Z',"
        "'running',clock_timestamp()) RETURNING id",
        (ids[f"client_{client}"], ids[f"project_{label}"], ids[f"repo_{label}"]),
    ).fetchone()[0]


def _snapshot(db, ids, run_id, number=1):
    import hashlib
    import json

    from psycopg.types.json import Jsonb

    payload = {"schema_version": 1, "snapshot": {"repository": "owner/repo-a", "pull": {"number": number}}}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return db.execute(
        f"INSERT INTO {SCHEMA}.pr_snapshots"
        "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
        "evaluator_version,payload,fingerprint)"
        " VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),'test-v1',%s,%s) RETURNING id",
        (ids["client_a"], ids["project_a"], ids["repo_a"], run_id, number, "a" * 40, Jsonb(payload), digest),
    ).fetchone()[0]


def _historical_rows(db, ids):
    run_id = _running(db, ids)
    snapshot = _snapshot(db, ids, run_id)
    db.execute(
        f"UPDATE {SCHEMA}.sync_runs SET status='succeeded',finished_at=clock_timestamp() WHERE id=%s",
        (run_id,),
    )
    report = db.execute(
        f"INSERT INTO {SCHEMA}.reports(client_id,project_id,window_start,window_end,content,complete)"
        " VALUES(%s,%s,'2026-10-01T00:00:00Z','2026-10-19T00:00:00Z','SYNTHETIC report',true) RETURNING id",
        (ids["client_a"], ids["project_a"]),
    ).fetchone()[0]
    source = db.execute(
        f"INSERT INTO {SCHEMA}.report_sources(client_id,project_id,report_id,sync_run_id,snapshot_id)"
        " VALUES(%s,%s,%s,%s,%s) RETURNING id",
        (ids["client_a"], ids["project_a"], report, run_id, snapshot),
    ).fetchone()[0]
    event = db.execute(
        f"INSERT INTO {SCHEMA}.audit_events(client_id,project_id,action,entity_id,actor_kind)"
        " VALUES(%s,%s,'report_created',%s,'system') RETURNING id",
        (ids["client_a"], ids["project_a"], report),
    ).fetchone()[0]
    return {"pr_snapshots": snapshot, "reports": report, "report_sources": source, "audit_events": event}


@pytest.mark.parametrize("table", ["pr_snapshots", "reports", "report_sources", "audit_events"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_history_cannot_be_changed_even_by_test_migration_admin(prepared_db, table, operation):
    db, ids = prepared_db
    records = _historical_rows(db, ids)
    query = (
        psycopg.sql.SQL("UPDATE {}.{} SET id=id WHERE id=%s")
        if operation == "UPDATE"
        else psycopg.sql.SQL("DELETE FROM {}.{} WHERE id=%s")
    ).format(psycopg.sql.Identifier(SCHEMA), psycopg.sql.Identifier(table))
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        db.execute(query, (records[table],))
    assert (
        db.execute(
            psycopg.sql.SQL("SELECT count(*) FROM {}.{} WHERE id=%s").format(
                psycopg.sql.Identifier(SCHEMA), psycopg.sql.Identifier(table)
            ),
            (records[table],),
        ).fetchone()[0]
        == 1
    )


def test_only_one_active_run_and_no_reopening_finished_execution(prepared_db):
    db, ids = prepared_db
    run_id = _running(db, ids)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _running(db, ids)
    db.execute(
        f"UPDATE {SCHEMA}.sync_runs SET status='partial',finished_at=clock_timestamp() WHERE id=%s",
        (run_id,),
    )
    assert _running(db, ids) != run_id
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        db.execute(f"UPDATE {SCHEMA}.sync_runs SET status='running',finished_at=NULL WHERE id=%s", (run_id,))


def test_invalid_window_and_invalid_status_timestamps_are_rejected(prepared_db):
    db, ids = prepared_db
    for start, end, status in [
        ("2026-10-19T00:00:00Z", "2026-10-01T00:00:00Z", "pending"),
        ("2026-10-01T00:00:00Z", "2026-10-19T00:00:00Z", "succeeded"),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                f"INSERT INTO {SCHEMA}.sync_runs"
                "(client_id,project_id,repository_id,window_start,window_end,status)"
                " VALUES(%s,%s,%s,%s,%s,%s)",
                (ids["client_a"], ids["project_a"], ids["repo_a"], start, end, status),
            )


def test_report_sources_cannot_reference_another_project(prepared_db):
    db, ids = prepared_db
    records = _historical_rows(db, ids)
    foreign_run = _running(db, ids, "b")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            f"INSERT INTO {SCHEMA}.report_sources(client_id,project_id,report_id,sync_run_id)"
            " VALUES(%s,%s,%s,%s)",
            (ids["client_a"], ids["project_a"], records["reports"], foreign_run),
        )


def test_snapshots_require_running_execution_and_are_unique_per_pull_in_run(prepared_db):
    db, ids = prepared_db
    run_id = _running(db, ids)
    first = _snapshot(db, ids, run_id)
    assert first is not None
    with pytest.raises(psycopg.errors.UniqueViolation):
        _snapshot(db, ids, run_id)
    db.execute(
        f"UPDATE {SCHEMA}.sync_runs SET status='failed',finished_at=clock_timestamp() WHERE id=%s",
        (run_id,),
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        _snapshot(db, ids, run_id, number=2)


def test_schema_creation_rolls_back_if_prerequisite_auth_table_missing(pg_admin):
    db = pg_admin
    assert db.info.host == "127.0.0.1" and db.info.dbname.startswith("ghi_test_")
    with pytest.raises(psycopg.errors.InvalidSchemaName):
        db.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
    db.rollback()
    assert db.execute("SELECT to_regnamespace('github_intelligence')").fetchone()[0] is None


def test_migration_works_under_non_superuser_creator_role(pg_admin):
    db = pg_admin
    assert db.info.host == "127.0.0.1" and db.info.dbname.startswith("ghi_test_")
    operator = "ghi_migration_test_" + uuid4().hex[:12]
    name = psycopg.sql.Identifier(operator)
    db.execute(
        psycopg.sql.SQL("CREATE ROLE {} LOGIN NOINHERIT CREATEROLE NOSUPERUSER NOBYPASSRLS").format(name)
    )
    db.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
    db.execute(
        psycopg.sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
            psycopg.sql.Identifier(db.info.dbname), name
        )
    )
    db.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA auth TO {}").format(name))
    db.execute(psycopg.sql.SQL("GRANT REFERENCES ON auth.users TO {}").format(name))
    if db.execute("SELECT 1 FROM pg_roles WHERE rolname='ghi_owner'").fetchone():
        db.execute(psycopg.sql.SQL("GRANT ghi_owner TO {} WITH ADMIN TRUE, SET TRUE").format(name))
    with psycopg.connect(db.info.dsn, user=operator, autocommit=True) as migration_connection:
        assert migration_connection.execute("SELECT current_user,session_user").fetchone() == (
            operator,
            operator,
        )
        assert migration_connection.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False,)
        migration_connection.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
    assert db.execute("SELECT count(*) FROM pg_tables WHERE schemaname=%s", (SCHEMA,)).fetchone()[0] == len(
        TABLES
    )
    assert db.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s", (SCHEMA,)
    ).fetchone() == ("ghi_owner",)
    assert db.execute(
        "SELECT has_database_privilege('ghi_owner',current_database(),'CREATE')"
    ).fetchone() == (False,)


def test_concurrent_workers_cannot_queue_same_repository_twice(prepared_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    db, ids = prepared_db
    barrier = Barrier(2)

    def enqueue():
        with psycopg.connect(db.info.dsn, autocommit=True) as connection:
            try:
                with connection.transaction():
                    set_scope(connection, ids)
                    connection.execute("SET LOCAL lock_timeout='5s'")
                    barrier.wait(timeout=5)
                    connection.execute(
                        f"INSERT INTO {SCHEMA}.sync_runs"
                        "(client_id,project_id,repository_id,window_start,window_end)"
                        " VALUES(%s,%s,%s,'2026-10-01T00:00:00Z','2026-10-19T00:00:00Z')",
                        (ids["client_a"], ids["project_a"], ids["repo_a"]),
                    )
                return "created"
            except psycopg.errors.UniqueViolation:
                return "busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(enqueue), pool.submit(enqueue)]
        assert sorted(future.result(timeout=15) for future in futures) == ["busy", "created"]
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.sync_runs").fetchone()[0] == 1


def test_public_transaction_interface_applies_scope_and_resets_it(prepared_db):
    from github_intelligence.storage import ProjectScope, project_transaction

    db, ids = prepared_db
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])
    with project_transaction(db, scope) as connection:
        assert connection.execute("SHOW search_path").fetchone() == ("pg_catalog",)
        assert connection.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == [(ids["repo_a"],)]
    assert db.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    with db.transaction():
        db.execute("SET LOCAL ROLE github_intelligence_runtime")
        assert db.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == []


def test_transaction_failure_is_sanitized_and_does_not_leave_role_or_scope(prepared_db):
    from github_intelligence.storage import ProjectScope, StorageError, project_transaction

    db, ids = prepared_db
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])
    with pytest.raises(StorageError) as error, project_transaction(db, scope) as connection:
        connection.execute("SELECT %s::integer", ("SYNTHETIC_SECRET_VALUE",))
    assert "SYNTHETIC_SECRET_VALUE" not in str(error.value)
    assert db.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert db.execute("SELECT current_user").fetchone()[0] == "postgres"


def test_transaction_rejects_nested_scope_and_unsafe_runtime_drift(prepared_db):
    from github_intelligence.storage import ProjectScope, StorageError, project_transaction

    db, ids = prepared_db
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])
    with db.transaction():
        with pytest.raises(StorageError), project_transaction(db, scope):
            pytest.fail("Existing transaction must not be reused")
    db.execute("ALTER ROLE github_intelligence_runtime BYPASSRLS")
    try:
        with pytest.raises(StorageError), project_transaction(db, scope):
            pytest.fail("Unsafe runtime role must fail closed")
    finally:
        db.execute("ALTER ROLE github_intelligence_runtime NOBYPASSRLS")


def test_persist_repository_snapshot_records_scoped_history_without_raw_body(prepared_db):
    import json
    from datetime import datetime, timezone

    from github_intelligence.models import (
        Evidence,
        PullRequestSnapshot,
        ReportWindow,
        RepositoryConfig,
        RepositorySnapshot,
    )
    from github_intelligence.persistence import persist_repository_snapshot
    from github_intelligence.storage import ProjectScope

    db, ids = prepared_db
    observed = datetime(2026, 10, 19, tzinfo=timezone.utc)
    window = ReportWindow(since=observed, until=datetime(2026, 10, 20, tzinfo=timezone.utc))
    config = RepositoryConfig(full_name="owner/repo-a", github_id=1001)
    pull = PullRequestSnapshot(
        repository=config.full_name,
        observed_at=observed,
        pull={
            "number": 1,
            "title": "SYNTHETIC pilot pull request",
            "body": "SYNTHETIC_SECRET_BODY",
            "state": "closed",
            "updated_at": "2026-10-19T00:00:00Z",
            "merged_at": "2026-10-19T00:00:00Z",
            "merged": True,
            "draft": False,
            "commits": 1,
            "head": {"sha": "a" * 40, "ref": "feature"},
            "base": {"sha": "b" * 40, "ref": "main"},
            "user": {"id": 1, "login": "author"},
        },
        evidence=[
            Evidence(
                kind="pull",
                url="https://github.com/owner/repo-a/pull/1",
                observed_at=observed,
            )
        ],
    )
    repository = RepositorySnapshot(
        config=config,
        observed_at=observed,
        pulls=[pull],
        complete=True,
        authenticated=True,
        github_id=1001,
    )
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])

    result = persist_repository_snapshot(
        db,
        scope,
        repository,
        window,
        "SYNTHETIC report",
        github_app_id=1,
        github_installation_id=1,
    )

    assert result.status == "succeeded"
    assert len(result.snapshot_ids) == 1
    assert db.execute(
        f"SELECT status FROM {SCHEMA}.sync_runs WHERE id=%s", (result.sync_run_id,)
    ).fetchone() == ("succeeded",)
    stored = db.execute(
        f"SELECT payload FROM {SCHEMA}.pr_snapshots WHERE id=%s", (result.snapshot_ids[0],)
    ).fetchone()[0]
    assert "SYNTHETIC_SECRET_BODY" not in json.dumps(stored)
    assert db.execute(
        f"SELECT content,complete FROM {SCHEMA}.reports WHERE id=%s", (result.report_id,)
    ).fetchone() == ("SYNTHETIC report", True)
    assert db.execute(
        f"SELECT sync_run_id,snapshot_id FROM {SCHEMA}.report_sources WHERE report_id=%s",
        (result.report_id,),
    ).fetchall() == [(result.sync_run_id, result.snapshot_ids[0])]


def test_persist_repository_snapshot_marks_incomplete_collection_partial(prepared_db):
    from datetime import datetime, timezone

    from github_intelligence.models import ReportWindow, RepositoryConfig, RepositorySnapshot
    from github_intelligence.persistence import persist_repository_snapshot
    from github_intelligence.storage import ProjectScope

    db, ids = prepared_db
    observed = datetime(2026, 10, 19, tzinfo=timezone.utc)
    repository = RepositorySnapshot(
        config=RepositoryConfig(full_name="owner/repo-a", github_id=1001),
        observed_at=observed,
        complete=False,
        limitations=["SYNTHETIC unavailable source"],
        github_id=1001,
    )
    result = persist_repository_snapshot(
        db,
        ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"]),
        repository,
        ReportWindow(since=observed, until=datetime(2026, 10, 20, tzinfo=timezone.utc)),
        "SYNTHETIC partial report",
        github_app_id=1,
        github_installation_id=1,
    )

    assert result.status == "partial"
    assert db.execute(
        f"SELECT status,error_code FROM {SCHEMA}.sync_runs WHERE id=%s", (result.sync_run_id,)
    ).fetchone() == ("partial", None)
    assert db.execute(
        f"SELECT complete FROM {SCHEMA}.reports WHERE id=%s", (result.report_id,)
    ).fetchone() == (False,)


def test_persist_failure_rolls_back_report_and_marks_run_failed(prepared_db):
    from datetime import datetime, timezone

    from github_intelligence.models import (
        Evidence,
        PullRequestSnapshot,
        ReportWindow,
        RepositoryConfig,
        RepositorySnapshot,
    )
    from github_intelligence.persistence import persist_repository_snapshot
    from github_intelligence.storage import ProjectScope, StorageError

    db, ids = prepared_db
    observed = datetime(2026, 10, 19, tzinfo=timezone.utc)
    config = RepositoryConfig(full_name="owner/repo-a", github_id=1001)
    repository = RepositorySnapshot(
        config=config,
        observed_at=observed,
        pulls=[
            PullRequestSnapshot(
                repository=config.full_name,
                observed_at=observed,
                pull={
                    "number": 1,
                    "title": "SYNTHETIC rollback",
                    "state": "closed",
                    "head": {"sha": "c" * 40},
                },
                evidence=[
                    Evidence(
                        kind="pull",
                        url="https://github.com/owner/repo-a/pull/1",
                        observed_at=observed,
                    )
                ],
            )
        ],
        github_id=1001,
    )
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])
    db.execute(f"REVOKE INSERT ON {SCHEMA}.reports FROM github_intelligence_runtime")
    try:
        with pytest.raises(StorageError, match="marked failed"):
            persist_repository_snapshot(
                db,
                scope,
                repository,
                ReportWindow(since=observed, until=datetime(2026, 10, 20, tzinfo=timezone.utc)),
                "SYNTHETIC report that must roll back",
                github_app_id=1,
                github_installation_id=1,
            )
    finally:
        db.execute(f"GRANT INSERT ON {SCHEMA}.reports TO github_intelligence_runtime")

    run = db.execute(
        f"SELECT status,error_code FROM {SCHEMA}.sync_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert run == ("failed", "PERSISTENCE_FAILED")
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.reports").fetchone() == (0,)
    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.pr_snapshots").fetchone() == (0,)


def test_persist_repository_snapshot_rejects_different_github_installation(prepared_db):
    from datetime import datetime, timezone

    from github_intelligence.models import ReportWindow, RepositoryConfig, RepositorySnapshot
    from github_intelligence.persistence import persist_repository_snapshot
    from github_intelligence.storage import ProjectScope, StorageError

    db, ids = prepared_db
    observed = datetime(2026, 10, 19, tzinfo=timezone.utc)
    repository = RepositorySnapshot(
        config=RepositoryConfig(full_name="owner/repo-a", github_id=1001),
        observed_at=observed,
        github_id=1001,
    )

    with pytest.raises(StorageError, match="not pre-provisioned"):
        persist_repository_snapshot(
            db,
            ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"]),
            repository,
            ReportWindow(since=observed, until=datetime(2026, 10, 20, tzinfo=timezone.utc)),
            "SYNTHETIC report",
            github_app_id=1,
            github_installation_id=999,
        )

    assert db.execute(f"SELECT count(*) FROM {SCHEMA}.sync_runs").fetchone() == (0,)


def test_scoped_persistence_roundtrip_preserves_evidence_without_raw_bodies(prepared_db):
    import hashlib
    import json
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from github_intelligence.evaluate import evaluate
    from github_intelligence.models import Evidence, PullRequestSnapshot, RepositoryConfig
    from github_intelligence.persistence_payload import restore_snapshot, snapshot_payload
    from github_intelligence.storage import ProjectScope, project_transaction

    db, ids = prepared_db
    observed = datetime(2026, 10, 19, tzinfo=timezone.utc)
    original = PullRequestSnapshot(
        repository="owner/repo-a",
        observed_at=observed,
        pull={
            "number": 1,
            "title": "SYNTHETIC integration",
            "body": "SYNTHETIC_SECRET_BODY",
            "state": "closed",
            "merged_at": "2026-10-18T12:00:00Z",
            "merged": True,
            "draft": False,
            "head": {"sha": "a" * 40, "ref": "feature"},
            "base": {"sha": "b" * 40, "ref": "main"},
            "user": {"id": 1, "login": "author"},
        },
        evidence=[Evidence(kind="pull", url="https://github.com/owner/repo-a/pull/1", observed_at=observed)],
    )
    scope = ProjectScope(client_id=ids["client_a"], project_id=ids["project_a"])
    payload = snapshot_payload(original)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    run_id = _running(db, ids)
    with project_transaction(db, scope) as connection:
        snapshot_id = connection.execute(
            f"INSERT INTO {SCHEMA}.pr_snapshots"
            "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
            "evaluator_version,payload,fingerprint) VALUES(%s,%s,%s,%s,1,%s,%s,'test-v1',%s,%s) RETURNING id",
            (
                ids["client_a"],
                ids["project_a"],
                ids["repo_a"],
                run_id,
                "a" * 40,
                observed,
                Jsonb(payload),
                digest,
            ),
        ).fetchone()[0]
    # A second real connection demonstrates persistence beyond a transaction/session.
    with psycopg.connect(db.info.dsn, autocommit=True) as second:
        with project_transaction(second, scope) as connection:
            stored = connection.execute(
                f"SELECT payload FROM {SCHEMA}.pr_snapshots WHERE id=%s", (snapshot_id,)
            ).fetchone()[0]
    assert "SYNTHETIC_SECRET_BODY" not in json.dumps(stored)
    restored = restore_snapshot(stored)
    assert restored.evidence == original.evidence
    assessment = evaluate(restored, RepositoryConfig(full_name="owner/repo-a"))
    assert assessment.lifecycle == "merged"
    assert assessment.deployment == assessment.validation == "unknown"
