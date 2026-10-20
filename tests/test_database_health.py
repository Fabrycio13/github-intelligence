"""Runtime diagnostic exercised against an actual disposable PostgreSQL server."""

import psycopg
import pytest
from test_storage import prepared_db as prepared_db


@pytest.fixture
def runtime_database(prepared_db):
    admin, ids = prepared_db
    assert admin.info.host == "127.0.0.1" and admin.info.dbname.startswith("ghi_test_")
    if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname='github_intelligence_worker'").fetchone():
        admin.execute(
            "CREATE ROLE github_intelligence_worker LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS"
            " NOCREATEDB NOCREATEROLE NOREPLICATION"
        )
    admin.execute(
        "GRANT github_intelligence_runtime TO github_intelligence_worker WITH INHERIT FALSE, SET TRUE"
    )
    with psycopg.connect(admin.info.dsn, user="github_intelligence_worker", autocommit=True) as runtime:
        yield admin, runtime, ids


def test_db_check_accepts_restricted_login_without_project_context(runtime_database):
    from github_intelligence.db_health import check_database

    admin, runtime, _ = runtime_database
    health = check_database(runtime)
    assert health.login_role == "github_intelligence_worker"
    assert health.database == admin.info.dbname
    assert health.tables_checked == 10
    assert runtime.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert runtime.execute("SELECT current_user").fetchone() == ("github_intelligence_worker",)


def test_db_check_rejects_admin_and_masked_admin(runtime_database):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, _, _ = runtime_database
    with pytest.raises(StorageError):
        check_database(admin)
    admin.execute("SET ROLE github_intelligence_worker")
    try:
        with pytest.raises(StorageError):
            check_database(admin)
    finally:
        admin.execute("RESET ROLE")


def test_db_check_rejects_bypass_drift(runtime_database):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute("ALTER ROLE github_intelligence_runtime BYPASSRLS")
    try:
        with pytest.raises(StorageError):
            check_database(runtime)
        assert runtime.execute("SELECT current_user").fetchone() == ("github_intelligence_worker",)
    finally:
        admin.execute("ALTER ROLE github_intelligence_runtime NOBYPASSRLS")


@pytest.mark.parametrize(
    "tamper",
    [
        "ALTER TABLE github_intelligence.repositories DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE github_intelligence.repositories NO FORCE ROW LEVEL SECURITY",
        "ALTER POLICY worker_scope ON github_intelligence.repositories USING (true) WITH CHECK (true)",
        "GRANT DELETE ON github_intelligence.repositories TO github_intelligence_runtime",
    ],
)
def test_db_check_rejects_schema_or_policy_drift(runtime_database, tamper):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute(tamper)
    with pytest.raises(StorageError):
        check_database(runtime)


def test_db_check_rejects_context_inherited_from_another_operation(runtime_database):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    _, runtime, ids = runtime_database
    runtime.execute("SELECT set_config('ghi.client_id',%s,false)", (str(ids["client_a"]),))
    with pytest.raises(StorageError):
        check_database(runtime)


def test_db_check_rejects_nested_transaction(runtime_database):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    _, runtime, _ = runtime_database
    with runtime.transaction():
        with pytest.raises(StorageError):
            check_database(runtime)


@pytest.mark.parametrize("privilege", ["DELETE", "TRUNCATE"])
def test_db_check_rejects_destructive_privilege_granted_directly_to_login(runtime_database, privilege):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute(
        psycopg.sql.SQL("GRANT {} ON github_intelligence.repositories TO github_intelligence_worker").format(
            psycopg.sql.SQL(privilege)
        )
    )
    with pytest.raises(StorageError):
        check_database(runtime)


def test_db_check_rejects_inheritable_runtime_membership_despite_login_noinherit(runtime_database):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute(
        "GRANT github_intelligence_runtime TO github_intelligence_worker WITH INHERIT TRUE, SET TRUE"
    )
    try:
        assert admin.execute(
            "SELECT rolinherit FROM pg_roles WHERE rolname='github_intelligence_worker'"
        ).fetchone() == (False,)
        with pytest.raises(StorageError):
            check_database(runtime)
    finally:
        admin.execute(
            "GRANT github_intelligence_runtime TO github_intelligence_worker WITH INHERIT FALSE, SET TRUE"
        )


@pytest.mark.parametrize("browser_role", ["anon", "authenticated", "service_role"])
@pytest.mark.parametrize("group", ["github_intelligence_runtime", "ghi_owner"])
def test_db_check_rejects_browser_membership_even_without_inheritance(runtime_database, browser_role, group):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute(
        psycopg.sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
            psycopg.sql.Identifier(group), psycopg.sql.Identifier(browser_role)
        )
    )
    try:
        with pytest.raises(StorageError):
            check_database(runtime)
    finally:
        admin.execute(
            psycopg.sql.SQL("REVOKE {} FROM {}").format(
                psycopg.sql.Identifier(group), psycopg.sql.Identifier(browser_role)
            )
        )


@pytest.mark.parametrize("browser_role", ["anon", "authenticated", "service_role"])
@pytest.mark.parametrize(
    "grant",
    [
        "GRANT USAGE ON SCHEMA github_intelligence TO {}",
        "GRANT SELECT ON github_intelligence.repositories TO {}",
        "GRANT SELECT (id) ON github_intelligence.repositories TO {}",
        "GRANT EXECUTE ON FUNCTION github_intelligence.client_context() TO {}",
    ],
)
def test_db_check_rejects_browser_schema_table_column_or_function_grants(
    runtime_database, browser_role, grant
):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute(psycopg.sql.SQL(grant).format(psycopg.sql.Identifier(browser_role)))
    with pytest.raises(StorageError):
        check_database(runtime)


def test_db_check_rejects_role_administration_rights_on_worker(runtime_database):
    from github_intelligence.db_health import check_database
    from github_intelligence.storage import StorageError

    admin, runtime, _ = runtime_database
    admin.execute(
        "GRANT github_intelligence_runtime TO github_intelligence_worker"
        " WITH ADMIN TRUE, INHERIT FALSE, SET TRUE"
    )
    try:
        with pytest.raises(StorageError):
            check_database(runtime)
    finally:
        admin.execute(
            "GRANT github_intelligence_runtime TO github_intelligence_worker"
            " WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        )
