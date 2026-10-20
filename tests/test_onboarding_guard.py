"""Security-definer lock helper under the actual restricted PostgreSQL login."""
import psycopg
import pytest
from test_repository_binding import apply_draft, binding_rows, factory
from test_repository_binding import binding_db as binding_db

from github_intelligence.db_health import check_database
from github_intelligence.storage import StorageError, project_transaction

FUNCTION = "github_intelligence.lock_repository_binding_scope(bigint,bigint)"


def test_guard_is_scoped_and_has_no_direct_worker_or_browser_execute_grant(binding_db):
    db, scopes = binding_db
    apply_draft(db)
    before = binding_rows(db)
    for role in ("anon", "authenticated", "service_role", "github_intelligence_worker"):
        assert db.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, FUNCTION),
        ).fetchone() == (False,)
    assert db.execute(
        "SELECT has_function_privilege('github_intelligence_runtime',%s,'EXECUTE')", (FUNCTION,),
    ).fetchone() == (True,)
    with factory(db)() as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT github_intelligence.lock_repository_binding_scope(11,21)")
        with worker.transaction():
            worker.execute("SET LOCAL ROLE github_intelligence_runtime")
            assert worker.execute(
                "SELECT github_intelligence.lock_repository_binding_scope(11,21)"
            ).fetchone() == (False,)
        with project_transaction(worker, scopes[0]):
            assert worker.execute(
                "SELECT github_intelligence.lock_repository_binding_scope(11,21)"
            ).fetchone() == (True,)
            assert worker.execute(
                "SELECT github_intelligence.lock_repository_binding_scope(NULL,21)"
            ).fetchone() == (False,)
            for table in ("clients", "projects", "github_installations"):
                assert worker.execute(
                    "SELECT has_any_column_privilege(current_user,%s,'UPDATE')",
                    (f"github_intelligence.{table}",),
                ).fetchone() == (False,)
    assert binding_rows(db) == before


@pytest.mark.parametrize("change", [
    f"ALTER FUNCTION {FUNCTION} SECURITY INVOKER",
    f"ALTER FUNCTION {FUNCTION} SET search_path=public",
    f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO anon",
    f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO github_intelligence_worker",
    *(f"GRANT UPDATE(status) ON github_intelligence.{table} TO {role}"
      for table in ("clients", "projects", "github_installations")
      for role in ("github_intelligence_runtime", "github_intelligence_worker")),
    "ALTER POLICY onboarding_owner_lock ON github_intelligence.clients USING(true) WITH CHECK(true)",
    "DROP POLICY onboarding_owner_lock ON github_intelligence.projects",
])
def test_health_rejects_lock_helper_or_owner_policy_drift(binding_db, change):
    db, _ = binding_db
    apply_draft(db)
    with factory(db)() as worker:
        assert check_database(worker).tables_checked == 10
    db.execute(change)
    with factory(db)() as worker, pytest.raises(StorageError):
        check_database(worker)


@pytest.mark.parametrize("flag", ["LOGIN", "SUPERUSER", "BYPASSRLS"])
@pytest.mark.parametrize("stage", ["before_migration", "after_migration"])
def test_owner_privilege_drift_is_rejected(binding_db, flag, stage):
    db, _ = binding_db
    if stage == "after_migration":
        apply_draft(db)
    db.execute(f"ALTER ROLE ghi_owner {flag}")
    try:
        if stage == "before_migration":
            with pytest.raises(psycopg.errors.RaiseException):
                apply_draft(db)
            db.rollback()
        else:
            with factory(db)() as worker, pytest.raises(StorageError):
                check_database(worker)
    finally:
        db.rollback()
        db.execute("ALTER ROLE ghi_owner NOLOGIN NOSUPERUSER NOBYPASSRLS")
