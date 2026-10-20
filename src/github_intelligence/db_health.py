"""Read-only database security diagnostic; not user authentication or a migration."""

import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row

from github_intelligence.storage import StorageError

_LOGIN = "github_intelligence_worker"
_TABLES = {
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
_REFUSED = "Configuração de segurança do banco divergente; operação recusada."


@dataclass(frozen=True)
class DatabaseHealth:
    database: str
    login_role: str
    tables_checked: int


def _reject_destructive_table_privileges(cursor: psycopg.Cursor[Any]) -> None:
    destructive = cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
        " WHERE n.nspname='github_intelligence' AND c.relkind IN ('r','p')"
        " AND (has_table_privilege(current_user,c.oid,'DELETE')"
        " OR has_table_privilege(current_user,c.oid,'TRUNCATE')))"
    ).fetchone()
    if destructive != (False,):
        raise StorageError(_REFUSED)


def _reject_browser_access(cursor: psycopg.Cursor[Any]) -> None:
    unsafe = cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_roles r JOIN pg_namespace scope"
        " ON scope.nspname='github_intelligence'"
        " WHERE r.rolname IN ('anon','authenticated','service_role')"
        " AND (pg_has_role(r.oid,'github_intelligence_runtime','MEMBER')"
        " OR pg_has_role(r.oid,'ghi_owner','MEMBER')"
        " OR has_schema_privilege(r.oid,scope.oid,'USAGE,CREATE')"
        " OR EXISTS(SELECT 1 FROM pg_class c WHERE c.relnamespace=scope.oid AND c.relkind IN ('r','p')"
        " AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
        " OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))"
        " OR EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace=scope.oid"
        " AND has_function_privilege(r.oid,p.oid,'EXECUTE'))))"
    ).fetchone()
    if unsafe != (False,):
        raise StorageError(_REFUSED)


def _reject_onboarding_privilege_drift(cursor: psycopg.Cursor[Any]) -> None:
    owner = cursor.execute(
        "SELECT rolcanlogin,rolinherit,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication"
        " FROM pg_roles WHERE rolname='ghi_owner'"
    ).fetchone()
    if owner is None or any(owner):
        raise StorageError(_REFUSED)
    update = cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_roles r CROSS JOIN pg_class c"
        " JOIN pg_namespace n ON n.oid=c.relnamespace"
        " WHERE r.rolname IN ('github_intelligence_worker','github_intelligence_runtime')"
        " AND n.nspname='github_intelligence' AND c.relkind='r'"
        " AND c.relname IN ('clients','projects','github_installations')"
        " AND has_any_column_privilege(r.oid,c.oid,'UPDATE'))"
    ).fetchone()
    direct_execute = cursor.execute(
        "SELECT COALESCE(has_function_privilege('github_intelligence_worker',"
        "to_regprocedure('github_intelligence.lock_repository_binding_scope(bigint,bigint)'),'EXECUTE'),false)"
    ).fetchone()
    if update != (False,) or direct_execute != (False,):
        raise StorageError(_REFUSED)


def check_database(connection: psycopg.Connection[Any]) -> DatabaseHealth:
    """Validate a dedicated runtime login without modifying rows or choosing a project.

    TLS is enforced by connect_database, not this connection-injected diagnostic.
    Absence of context must deny access even if real project records already exist.
    """
    if connection.closed or connection.info.transaction_status != TransactionStatus.IDLE:
        raise StorageError("O diagnóstico exige uma conexão dedicada sem transação ativa.")
    try:
        with connection.transaction(), connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("SET LOCAL search_path = pg_catalog")
            row = cursor.execute(
                "SELECT current_database(),session_user,current_user,r.rolcanlogin,"
                "r.rolsuper,r.rolbypassrls,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolinherit,"
                "pg_has_role(current_user,'github_intelligence_runtime','SET'),"
                "pg_has_role(current_user,'ghi_owner','MEMBER'),"
                "has_schema_privilege(current_user,'github_intelligence','CREATE'),"
                "pg_has_role(current_user,'github_intelligence_runtime','USAGE'),"
                "EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid AND m.admin_option)"
                " FROM pg_roles r WHERE r.rolname=current_user"
            ).fetchone()
            if row is None:
                raise StorageError(_REFUSED)
            database, session, login, can_login, *permissions = row
            runtime_set = permissions[6]
            unsafe = permissions[:6] + permissions[7:]
            if (
                session != _LOGIN
                or login != _LOGIN
                or can_login is not True
                or any(unsafe)
                or not runtime_set
            ):
                raise StorageError(_REFUSED)
            context = cursor.execute(
                "SELECT nullif(current_setting('ghi.client_id',true),''),"
                "nullif(current_setting('ghi.project_id',true),'')"
            ).fetchone()
            if context != (None, None):
                raise StorageError("A conexão herdou contexto de projeto; diagnóstico recusado.")
            _reject_browser_access(cursor)
            _reject_destructive_table_privileges(cursor)
            cursor.execute("SET LOCAL ROLE github_intelligence_runtime")
            _reject_onboarding_privilege_drift(cursor)
            flags = cursor.execute(
                "SELECT r.rolcanlogin,r.rolsuper,r.rolbypassrls,r.rolcreatedb,r.rolcreaterole,"
                "r.rolreplication,r.rolinherit,pg_has_role(current_user,'ghi_owner','MEMBER'),"
                "has_schema_privilege(current_user,'github_intelligence','CREATE')"
                " FROM pg_roles r WHERE r.rolname=current_user"
            ).fetchone()
            if flags is None or any(flags):
                raise StorageError(_REFUSED)
            tables = cursor.execute(
                "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner)"
                " FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
                " WHERE n.nspname='github_intelligence' AND c.relkind IN ('r','p')"
            ).fetchall()
            if {row[0] for row in tables} != _TABLES or any(
                not row[1] or not row[2] or row[3] != "ghi_owner" for row in tables
            ):
                raise StorageError(_REFUSED)
            policies = cursor.execute(
                "SELECT tablename,roles,cmd,qual,with_check FROM pg_policies"
                " WHERE schemaname='github_intelligence'"
            ).fetchall()
            runtime_policies = [row for row in policies if row[1] == ["github_intelligence_runtime"]]
            owner_policies = [row for row in policies if row[1] != ["github_intelligence_runtime"]]
            if len(runtime_policies) != len(_TABLES) or {row[0] for row in runtime_policies} != _TABLES:
                raise StorageError(_REFUSED)
            if owner_policies:
                if len(owner_policies) != 3 or {row[0] for row in owner_policies} != {
                    "clients", "projects", "github_installations",
                } or any(row[1] != ["ghi_owner"] for row in owner_policies):
                    raise StorageError(_REFUSED)
                helper = cursor.execute(
                    "SELECT pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,"
                    "p.prorettype='pg_catalog.bool'::regtype,"
                    "has_function_privilege(current_user,p.oid,'EXECUTE') FROM pg_proc p"
                    " WHERE p.oid=to_regprocedure("
                    "'github_intelligence.lock_repository_binding_scope(bigint,bigint)')"
                ).fetchone()
                if helper != ("ghi_owner", True, ["search_path=pg_catalog, pg_temp"], True, True):
                    raise StorageError(_REFUSED)
            for table, roles, command, using, check in policies:
                client = "id" if table == "clients" else "client_id"
                expected = f"{client}=github_intelligence.client_context"
                if table not in {"clients", "github_installations"}:
                    project = "id" if table == "projects" else "project_id"
                    expected += f"AND{project}=github_intelligence.project_context"
                if (
                    roles not in (["github_intelligence_runtime"], ["ghi_owner"])
                    or command != "ALL"
                    or using != check
                    or not isinstance(using, str)
                    or re.sub(r"[\s()]+", "", using) != expected
                ):
                    raise StorageError(_REFUSED)
            _reject_destructive_table_privileges(cursor)
            for table in sorted(_TABLES):
                visible = cursor.execute(
                    psycopg.sql.SQL("SELECT EXISTS(SELECT 1 FROM github_intelligence.{})").format(
                        psycopg.sql.Identifier(table)
                    )
                ).fetchone()
                if visible != (False,):
                    raise StorageError(_REFUSED)
        return DatabaseHealth(database=database, login_role=login, tables_checked=len(tables))
    except psycopg.Error:
        raise StorageError("Falha no diagnóstico do banco; detalhes sensíveis foram omitidos.") from None
