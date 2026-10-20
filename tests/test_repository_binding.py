"""Repository onboarding at its public seam, using disposable loopback PostgreSQL."""

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from test_database_health import runtime_database as runtime_database
from test_storage import prepared_db as prepared_db

from github_intelligence.storage import ProjectScope, StorageError

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SCHEMA = "github_intelligence"
WORKER = "github_intelligence_worker"
VERIFIED = {
    "status": "ready",
    "full_name": "synthetic/repository",
    "github_id": 2001,
    "app_id": 11,
    "installation_id": 21,
    "main_branch": "main",
}


@pytest.fixture
def binding_db(pg_admin):
    db = pg_admin
    assert db.info.host == "127.0.0.1" and db.info.dbname.startswith("ghi_test_")
    db.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
    db.execute((MIGRATIONS / "0001_initial.sql").read_text(encoding="utf-8"), prepare=False)
    if not db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (WORKER,)).fetchone():
        db.execute((MIGRATIONS / "0002_runtime_login.sql").read_text(encoding="utf-8"), prepare=False)
    scopes = [ProjectScope(client_id=uuid4(), project_id=uuid4()) for _ in range(2)]
    scopes.append(ProjectScope(client_id=scopes[0].client_id, project_id=uuid4()))
    for scope in scopes[:2]:
        db.execute(
            f"INSERT INTO {SCHEMA}.clients(id,name) VALUES(%s,'SYNTHETIC client')",
            (scope.client_id,),
        )
    for scope in scopes:
        db.execute(
            f"INSERT INTO {SCHEMA}.projects(id,client_id,name) VALUES(%s,%s,'SYNTHETIC project')",
            (scope.project_id, scope.client_id),
        )
    return db, scopes


def factory(db):
    @contextmanager
    def connect():
        with psycopg.connect(db.info.dsn, user=WORKER, autocommit=True) as connection:
            yield connection

    return connect


def binder(db, scope):
    from github_intelligence.repository_binding import RepositoryBinder

    return RepositoryBinder(factory(db), scope)


def apply_draft(db):
    db.execute((MIGRATIONS / "0003_repository_onboarding.sql").read_text(encoding="utf-8"), prepare=False)


def count(db, table):
    return db.execute(
        psycopg.sql.SQL("SELECT count(*) FROM github_intelligence.{}").format(psycopg.sql.Identifier(table))
    ).fetchone()[0]


def test_missing_insert_capability_is_safe_and_read_only(binding_db):
    from github_intelligence.repository_binding import ONBOARDING_PERMISSION_REQUIRED

    db, scopes = binding_db
    service = binder(db, scopes[0])
    assert service.supported() is False
    with pytest.raises(StorageError, match=ONBOARDING_PERMISSION_REQUIRED) as error:
        service.bind(VERIFIED)
    assert str(error.value) == ONBOARDING_PERMISSION_REQUIRED
    assert count(db, "github_installations") == count(db, "repositories") == 0


def test_draft_enables_scoped_binding_and_preserves_existing_settings(binding_db):
    db, scopes = binding_db
    apply_draft(db)
    service = binder(db, scopes[0])
    assert service.supported() is True
    assert count(db, "github_installations") == count(db, "repositories") == 0
    first = service.bind(VERIFIED)
    assert first == {
        "id": first["id"],
        "full_name": "synthetic/repository",
        "status": "active",
        "created": True,
        "installation_id": 21,
    }
    assert isinstance(first["id"], str)
    assert db.execute(
        f"SELECT status,main_branch,expected_approvals FROM {SCHEMA}.repositories WHERE id=%s",
        (first["id"],),
    ).fetchone() == ("active", "main", 2)
    assert db.execute(
        f"SELECT status,verified_at IS NOT NULL,verified_at <= clock_timestamp()"
        f" FROM {SCHEMA}.github_installations"
    ).fetchone() == ("active", True, True)
    db.execute(
        f"UPDATE {SCHEMA}.repositories SET main_branch='historical',staging_branch='stage',"
        "production_branch='prod',expected_approvals=5 WHERE id=%s",
        (first["id"],),
    )
    before = db.execute(f"SELECT * FROM {SCHEMA}.repositories").fetchall()
    installation_before = db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall()
    assert service.bind({**VERIFIED, "main_branch": "changed"}) == {**first, "created": False}
    assert db.execute(f"SELECT * FROM {SCHEMA}.repositories").fetchall() == before
    assert db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall() == installation_before


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "unavailable"},
        {"status": None},
        {"full_name": "https://github.com/o/r"},
        {"full_name": "owner/.."},
        {"full_name": 1},
        {"github_id": True},
        {"github_id": "2001"},
        {"github_id": 0},
        {"github_id": -1},
        {"github_id": 1.5},
        {"app_id": False},
        {"app_id": "11"},
        {"installation_id": True},
        {"installation_id": -21},
        {"main_branch": 1},
        {"main_branch": False},
        {"main_branch": []},
    ],
)
def test_invalid_verified_result_never_writes(binding_db, changes):
    db, scopes = binding_db
    apply_draft(db)
    with pytest.raises(StorageError, match="Invalid verified repository"):
        binder(db, scopes[0]).bind({**VERIFIED, **changes})
    assert count(db, "github_installations") == count(db, "repositories") == 0


@pytest.mark.parametrize("key", list(VERIFIED))
def test_missing_verified_fields_are_rejected(binding_db, key):
    db, scopes = binding_db
    apply_draft(db)
    payload = {k: value for k, value in VERIFIED.items() if k != key}
    with pytest.raises(StorageError, match="Invalid verified repository"):
        binder(db, scopes[0]).bind(payload)
    assert count(db, "github_installations") == count(db, "repositories") == 0


@pytest.mark.parametrize("kind", ["missing_client", "missing_project", "wrong_client", "client", "project"])
def test_requires_active_matching_scope_and_supported_does_not_write(binding_db, kind):
    db, scopes = binding_db
    apply_draft(db)
    scope = scopes[0]
    if kind == "missing_client":
        scope = ProjectScope(client_id=uuid4(), project_id=scope.project_id)
    elif kind == "missing_project":
        scope = ProjectScope(client_id=scope.client_id, project_id=uuid4())
    elif kind == "wrong_client":
        scope = ProjectScope(client_id=scopes[1].client_id, project_id=scope.project_id)
    else:
        table = "clients" if kind == "client" else "projects"
        identity = scope.client_id if kind == "client" else scope.project_id
        db.execute(f"UPDATE {SCHEMA}.{table} SET status='inactive' WHERE id=%s", (identity,))
    service = binder(db, scope)
    assert service.supported() is False
    with pytest.raises(StorageError, match="Active client and project"):
        service.bind(VERIFIED)
    assert count(db, "github_installations") == count(db, "repositories") == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("github_id", 2002),
        ("full_name", "synthetic/renamed"),
        ("full_name", "SYNTHETIC/REPOSITORY"),
        ("installation_id", 22),
        ("app_id", 12),
    ],
)
def test_identity_conflicts_roll_back_new_installation_and_preserve_rows(binding_db, field, value):
    db, scopes = binding_db
    apply_draft(db)
    service = binder(db, scopes[0])
    service.bind(VERIFIED)
    before = db.execute(f"SELECT * FROM {SCHEMA}.repositories").fetchall()
    installations = db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall()
    with pytest.raises(StorageError, match="conflicts"):
        service.bind({**VERIFIED, field: value})
    assert db.execute(f"SELECT * FROM {SCHEMA}.repositories").fetchall() == before
    assert db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall() == installations


@pytest.mark.parametrize("status", ["inactive", "removed"])
def test_existing_repository_is_never_reactivated(binding_db, status):
    db, scopes = binding_db
    apply_draft(db)
    service = binder(db, scopes[0])
    service.bind(VERIFIED)
    db.execute(f"UPDATE {SCHEMA}.repositories SET status=%s", (status,))
    before = db.execute(f"SELECT * FROM {SCHEMA}.repositories").fetchall()
    with pytest.raises(StorageError, match="conflicts"):
        service.bind(VERIFIED)
    assert db.execute(f"SELECT * FROM {SCHEMA}.repositories").fetchall() == before


@pytest.mark.parametrize("status", ["suspended", "revoked", "unknown"])
def test_existing_installation_is_never_reactivated(binding_db, status):
    db, scopes = binding_db
    apply_draft(db)
    db.execute(
        f"INSERT INTO {SCHEMA}.github_installations"
        "(client_id,github_app_id,github_installation_id,status) VALUES(%s,11,21,%s)",
        (scopes[0].client_id, status),
    )
    before = db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall()
    with pytest.raises(StorageError, match="conflicts"):
        binder(db, scopes[0]).bind(VERIFIED)
    assert db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall() == before
    assert count(db, "repositories") == 0


def test_same_provider_installation_can_be_mapped_per_client_without_bypassing_rls(binding_db):
    from uuid import UUID

    from github_intelligence.storage import project_transaction

    db, scopes = binding_db
    apply_draft(db)
    results = [binder(db, scope).bind(VERIFIED) for scope in scopes]
    assert all(result["created"] for result in results)
    assert len({result["id"] for result in results}) == 3
    assert count(db, "github_installations") == 2
    with factory(db)() as worker:
        with project_transaction(worker, scopes[0]):
            assert worker.execute(f"SELECT id FROM {SCHEMA}.repositories").fetchall() == [
                (UUID(results[0]["id"]),)
            ]
            assert worker.execute(f"SELECT client_id FROM {SCHEMA}.github_installations").fetchall() == [
                (scopes[0].client_id,)
            ]


@pytest.mark.parametrize("cross_project", [False, True])
def test_concurrent_binding_is_idempotent_and_serializes_shared_client_installations(
    binding_db, cross_project
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from github_intelligence.repository_binding import RepositoryBinder

    db, scopes = binding_db
    apply_draft(db)
    barrier = Barrier(2)

    @contextmanager
    def connect():
        with factory(db)() as connection:
            connection.execute("SET SESSION lock_timeout='5s'")
            barrier.wait(timeout=10)
            yield connection

    other = scopes[2] if cross_project else scopes[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(RepositoryBinder(connect, scope).bind, VERIFIED) for scope in [scopes[0], other]
        ]
        results = [future.result(timeout=20) for future in futures]
    if cross_project:
        assert all(result["created"] for result in results)
        assert count(db, "repositories") == 2
    else:
        assert sorted(result["created"] for result in results) == [False, True]
        assert results[0]["id"] == results[1]["id"]
        assert count(db, "repositories") == 1
    assert count(db, "github_installations") == 1


def test_mid_transaction_database_failure_rolls_back_and_omits_diagnostics(binding_db):
    db, scopes = binding_db
    apply_draft(db)
    db.execute(
        f"ALTER TABLE {SCHEMA}.repositories ADD CONSTRAINT synthetic_failure"
        " CHECK (full_name <> 'synthetic/repository')"
    )
    with pytest.raises(StorageError) as error:
        binder(db, scopes[0]).bind(VERIFIED)
    assert "synthetic" not in str(error.value)
    assert "CHECK" not in str(error.value)
    assert count(db, "github_installations") == count(db, "repositories") == 0


def test_null_main_branch_and_preexisting_verified_installation_are_preserved(binding_db):
    db, scopes = binding_db
    apply_draft(db)
    db.execute(
        f"INSERT INTO {SCHEMA}.github_installations"
        "(client_id,github_app_id,github_installation_id,verified_at) VALUES(%s,11,21,NULL)",
        (scopes[0].client_id,),
    )
    before = db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall()
    binder(db, scopes[0]).bind({**VERIFIED, "main_branch": None})
    assert db.execute(f"SELECT main_branch FROM {SCHEMA}.repositories").fetchone() == (None,)
    assert db.execute(f"SELECT * FROM {SCHEMA}.github_installations").fetchall() == before


EXPECTED_INSERTS = {
    ("github_installations", column)
    for column in ["client_id", "github_app_id", "github_installation_id", "status", "verified_at"]
} | {
    ("repositories", column)
    for column in [
        "client_id",
        "project_id",
        "installation_id",
        "github_repository_id",
        "full_name",
        "status",
        "main_branch",
        "expected_approvals",
    ]
}


def privileges(db):
    return set(
        db.execute(
            "SELECT c.relname,a.attname,privilege.name FROM pg_class c"
            " JOIN pg_namespace n ON n.oid=c.relnamespace"
            " JOIN pg_attribute a ON a.attrelid=c.oid"
            " CROSS JOIN (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) AS privilege(name)"
            " WHERE n.nspname='github_intelligence' AND c.relkind='r'"
            " AND a.attnum>0 AND NOT a.attisdropped"
            " AND has_column_privilege('github_intelligence_runtime',c.oid,a.attnum,privilege.name)"
        ).fetchall()
    )


def test_migration_grants_only_required_insert_columns_and_worker_is_not_admin(binding_db):
    from github_intelligence.db_health import check_database

    db, scopes = binding_db
    before = privileges(db)
    apply_draft(db)
    after = privileges(db)
    assert after - before == {(table, column, "INSERT") for table, column in EXPECTED_INSERTS}
    assert before <= after
    assert db.execute(
        "SELECT rolcanlogin,rolinherit,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication"
        " FROM pg_roles WHERE rolname='github_intelligence_worker'"
    ).fetchone() == (True, False, False, False, False, False, False)
    with factory(db)() as worker:
        assert check_database(worker).tables_checked == 10
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute(f"SELECT * FROM {SCHEMA}.repositories")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SET ROLE ghi_owner")
        assert worker.execute(
            "SELECT has_schema_privilege(current_user,'github_intelligence','CREATE')"
        ).fetchone() == (False,)
    assert binder(db, scopes[0]).supported() is True


@pytest.mark.parametrize("table,column", sorted(EXPECTED_INSERTS))
def test_missing_any_required_column_fails_closed_with_static_message(binding_db, table, column):
    from github_intelligence.repository_binding import ONBOARDING_PERMISSION_REQUIRED

    db, scopes = binding_db
    apply_draft(db)
    db.execute(
        psycopg.sql.SQL(
            "REVOKE INSERT ({}) ON github_intelligence.{} FROM github_intelligence_runtime"
        ).format(psycopg.sql.Identifier(column), psycopg.sql.Identifier(table))
    )
    service = binder(db, scopes[0])
    assert service.supported() is False
    with pytest.raises(StorageError) as error:
        service.bind(VERIFIED)
    assert str(error.value) == ONBOARDING_PERMISSION_REQUIRED
    assert count(db, "github_installations") == count(db, "repositories") == 0


@pytest.mark.parametrize("table", ["github_installations", "repositories"])
def test_runtime_insert_without_scope_is_denied_by_rls(binding_db, table):
    db, scopes = binding_db
    apply_draft(db)
    bound = binder(db, scopes[0]).bind(VERIFIED)
    installation = db.execute(
        f"SELECT installation_id FROM {SCHEMA}.repositories WHERE id=%s", (bound["id"],)
    ).fetchone()[0]
    with factory(db)() as worker, pytest.raises(psycopg.errors.InsufficientPrivilege), worker.transaction():
        worker.execute("SET LOCAL ROLE github_intelligence_runtime")
        if table == "github_installations":
            worker.execute(
                f"INSERT INTO {SCHEMA}.github_installations"
                "(client_id,github_app_id,github_installation_id) VALUES(%s,11,22)",
                (scopes[0].client_id,),
            )
        else:
            worker.execute(
                f"INSERT INTO {SCHEMA}.repositories"
                "(client_id,project_id,installation_id,github_repository_id,full_name)"
                " VALUES(%s,%s,%s,999,'synthetic/forbidden')",
                (scopes[0].client_id, scopes[0].project_id, installation),
            )
    assert count(db, "github_installations") == count(db, "repositories") == 1


@pytest.mark.parametrize("drift", ["rls", "schema_owner", "table_owner", "policy", "worker"])
def test_migration_preflight_rolls_back_grants_on_security_drift(binding_db, drift):
    db, _ = binding_db
    changes = {
        "rls": f"ALTER TABLE {SCHEMA}.repositories NO FORCE ROW LEVEL SECURITY",
        "schema_owner": f"ALTER SCHEMA {SCHEMA} OWNER TO postgres",
        "table_owner": f"ALTER TABLE {SCHEMA}.repositories OWNER TO postgres",
        "policy": f"ALTER POLICY worker_scope ON {SCHEMA}.repositories USING (true) WITH CHECK (true)",
        "worker": "ALTER ROLE github_intelligence_worker INHERIT",
    }
    db.execute(changes[drift])
    before = privileges(db)
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            apply_draft(db)
        db.rollback()
        assert privileges(db) == before
        assert count(db, "github_installations") == count(db, "repositories") == 0
    finally:
        db.rollback()
        if drift == "worker":
            db.execute("ALTER ROLE github_intelligence_worker NOINHERIT")


def test_draft_executes_as_non_superuser_operator(binding_db):
    db, scopes = binding_db
    operator_name = "ghi_onboarding_test_" + uuid4().hex[:12]
    operator = psycopg.sql.Identifier(operator_name)
    db.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS").format(operator))
    db.execute(psycopg.sql.SQL("GRANT ghi_owner TO {} WITH SET TRUE").format(operator))
    try:
        with psycopg.connect(db.info.dsn, user=operator_name, autocommit=True) as connection:
            assert connection.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False,)
            apply_draft(connection)
        assert binder(db, scopes[0]).bind(VERIFIED)["created"] is True
    finally:
        db.execute(psycopg.sql.SQL("REVOKE ghi_owner FROM {}").format(operator))
        db.execute(psycopg.sql.SQL("DROP ROLE {}").format(operator))


def test_supported_runs_in_read_only_database_transaction(binding_db):
    from github_intelligence.repository_binding import RepositoryBinder

    db, scopes = binding_db
    apply_draft(db)

    @contextmanager
    def connect():
        with factory(db)() as connection:
            connection.execute("SET SESSION default_transaction_read_only=on")
            yield connection

    assert RepositoryBinder(connect, scopes[0]).supported() is True
    assert count(db, "github_installations") == count(db, "repositories") == 0


@pytest.mark.parametrize("operation", ["bind", "supported"])
def test_connection_failure_is_sanitized_before_transaction_starts(binding_db, operation):
    from github_intelligence.repository_binding import RepositoryBinder

    db, scopes = binding_db

    @contextmanager
    def connect():
        with psycopg.connect(
            db.info.dsn, dbname="ghi_test_SYNTHETIC_SECRET_missing", user=WORKER, autocommit=True
        ) as connection:
            yield connection

    service = RepositoryBinder(connect, scopes[0])
    with pytest.raises(StorageError) as error:
        if operation == "bind":
            service.bind(VERIFIED)
        else:
            service.supported()
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert "127.0.0.1" not in str(error.value)


def binding_rows(db):
    """Full records, not counts: failed binds must preserve existing settings."""
    return {
        table: db.execute(f"SELECT * FROM {SCHEMA}.{table} ORDER BY id").fetchall()
        for table in ("github_installations", "repositories")
    }


@pytest.mark.parametrize("table", ["clients", "projects", "github_installations"])
def test_deactivation_committed_while_binding_waits_for_advisory_lock_is_rejected(binding_db, table):
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from time import monotonic, sleep

    from github_intelligence.repository_binding import RepositoryBinder

    db, scopes = binding_db
    apply_draft(db)
    scope = scopes[0]
    if table == "github_installations":
        db.execute(
            f"INSERT INTO {SCHEMA}.github_installations"
            "(client_id,github_app_id,github_installation_id) VALUES(%s,11,21)",
            (scope.client_id,),
        )
    pids = Queue()

    @contextmanager
    def connect():
        with factory(db)() as connection:
            connection.execute("SET SESSION lock_timeout='5s'")
            pids.put(connection.info.backend_pid)
            yield connection

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction():
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"repository_binding:{scope.client_id}",),
            )
            future = pool.submit(RepositoryBinder(connect, scope).bind, VERIFIED)
            pid = pids.get(timeout=5)
            deadline = monotonic() + 5
            while not db.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks"
                " WHERE pid=%s AND locktype='advisory' AND NOT granted)",
                (pid,),
            ).fetchone()[0]:
                assert monotonic() < deadline, "worker did not wait for the advisory lock"
                sleep(0.01)
            identity = scope.client_id if table == "clients" else scope.project_id
            if table == "github_installations":
                identity = db.execute(f"SELECT id FROM {SCHEMA}.github_installations").fetchone()[0]
            status = "revoked" if table == "github_installations" else "inactive"
            db.execute(f"UPDATE {SCHEMA}.{table} SET status=%s WHERE id=%s", (status, identity))
            before = binding_rows(db)
        message = "conflicts" if table == "github_installations" else "Active client and project"
        with pytest.raises(StorageError, match=message):
            future.result(timeout=10)
    assert binding_rows(db) == before


@pytest.mark.parametrize("table", ["clients", "projects", "github_installations"])
@pytest.mark.parametrize("pause_after_insert", [False, True], ids=["before_insert", "before_commit"])
def test_inflight_binding_blocks_admin_status_change_until_commit(binding_db, table, pause_after_insert):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from github_intelligence.repository_binding import RepositoryBinder

    db, scopes = binding_db
    apply_draft(db)
    scope = scopes[0]
    installation_id = db.execute(
        f"INSERT INTO {SCHEMA}.github_installations"
        "(client_id,github_app_id,github_installation_id) VALUES(%s,11,21) RETURNING id",
        (scope.client_id,),
    ).fetchone()[0]
    identity = {
        "clients": scope.client_id,
        "projects": scope.project_id,
        "github_installations": installation_id,
    }[table]
    status = "revoked" if table == "github_installations" else "inactive"
    paused, release = Event(), Event()

    class PausingCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            prefix = (
                "INSERT INTO github_intelligence.repositories"
                if pause_after_insert
                else "SELECT id,full_name,status"
            )
            if isinstance(query, str) and query.startswith(prefix):
                paused.set()
                assert release.wait(timeout=10), "test did not release binder transaction"
            return result

    @contextmanager
    def connect():
        with psycopg.connect(
            db.info.dsn, user=WORKER, autocommit=True, cursor_factory=PausingCursor
        ) as worker:
            assert worker.execute("SELECT current_user,session_user").fetchone() == (WORKER, WORKER)
            worker.execute("SET SESSION lock_timeout='2s'")
            yield worker

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(RepositoryBinder(connect, scope).bind, VERIFIED)
        try:
            assert paused.wait(timeout=10), "binder did not reach the in-flight checkpoint"
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with db.transaction():
                    db.execute("SET LOCAL lock_timeout='200ms'")
                    db.execute(f"UPDATE {SCHEMA}.{table} SET status=%s WHERE id=%s", (status, identity))
            assert db.execute(f"SELECT status FROM {SCHEMA}.{table} WHERE id=%s", (identity,)).fetchone() == (
                "active",
            )
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["created"] is True
    assert db.execute(
        f"SELECT full_name,installation_id FROM {SCHEMA}.repositories WHERE id=%s", (result["id"],)
    ).fetchone() == (VERIFIED["full_name"], installation_id)
    with db.transaction():
        db.execute("SET LOCAL lock_timeout='200ms'")
        db.execute(f"UPDATE {SCHEMA}.{table} SET status=%s WHERE id=%s", (status, identity))
    assert db.execute(f"SELECT status FROM {SCHEMA}.{table} WHERE id=%s", (identity,)).fetchone() == (status,)


@pytest.mark.parametrize("table", ["clients", "projects", "github_installations"])
def test_restricted_runtime_cannot_take_status_row_locks_without_update_grants(runtime_database, table):
    admin, runtime, ids = runtime_database
    apply_draft(admin)
    assert runtime.execute("SELECT current_user,session_user").fetchone() == (WORKER, WORKER)
    with runtime.transaction():
        runtime.execute("SET LOCAL ROLE github_intelligence_runtime")
        runtime.execute("SET LOCAL lock_timeout='200ms'")
        runtime.execute(
            "SELECT set_config('ghi.client_id',%s,true),set_config('ghi.project_id',%s,true)",
            (str(ids["client_a"]), str(ids["project_a"])),
        )
        assert runtime.execute(
            "SELECT has_any_column_privilege(current_user,%s,'UPDATE')", (f"{SCHEMA}.{table}",)
        ).fetchone() == (False,)
        assert runtime.execute(f"SELECT id FROM {SCHEMA}.{table}").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error, runtime.transaction():
            runtime.execute(f"SELECT id FROM {SCHEMA}.{table} FOR SHARE")
        assert error.value.sqlstate == "42501"
