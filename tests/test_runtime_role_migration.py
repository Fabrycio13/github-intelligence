"""Runtime role provisioning SQL tested on a disposable local PostgreSQL."""

from pathlib import Path

import psycopg
import pytest

pytest_plugins = ("test_storage",)


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0002_runtime_login.sql"
WORKER = "github_intelligence_worker"


def test_runtime_login_migration_creates_restricted_login_without_password(prepared_db):
    admin, _ = prepared_db
    if admin.execute(
        "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s", (WORKER,)
    ).fetchone():
        admin.execute(f"REVOKE github_intelligence_runtime FROM {WORKER}")
        admin.execute(f"DROP ROLE {WORKER}")
    sql = MIGRATION.read_text(encoding="utf-8")
    admin.execute(sql, prepare=False)
    try:
        role = admin.execute(
            "SELECT rolcanlogin, rolinherit, rolsuper, rolbypassrls, rolcreatedb, "
            "rolcreaterole, rolreplication, rolpassword "
            "FROM pg_catalog.pg_authid WHERE rolname=%s",
            (WORKER,),
        ).fetchone()
        assert role == (True, False, False, False, False, False, False, None)

        membership = admin.execute(
            "SELECT am.admin_option, am.inherit_option, am.set_option "
            "FROM pg_catalog.pg_auth_members am "
            "JOIN pg_catalog.pg_roles parent ON parent.oid=am.roleid "
            "JOIN pg_catalog.pg_roles member ON member.oid=am.member "
            "WHERE parent.rolname='github_intelligence_runtime' AND member.rolname=%s",
            (WORKER,),
        ).fetchone()
        assert membership == (False, False, True)

        with pytest.raises(psycopg.Error, match="Worker role already exists"):
            admin.execute(sql, prepare=False)
        admin.rollback()
        assert admin.execute(
            "SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname=%s", (WORKER,)
        ).fetchone() == (None,)
    finally:
        admin.execute(f"REVOKE github_intelligence_runtime FROM {WORKER}")
        admin.execute(f"DROP ROLE {WORKER}")
