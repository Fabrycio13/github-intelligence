"""Scoped PostgreSQL persistence; no remote migrations or connection secrets here."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row
from pydantic import BaseModel, ConfigDict


class ProjectScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    client_id: UUID
    project_id: UUID


class StorageError(Exception):
    """Safe persistence failure; never include driver diagnostics or query values."""


@contextmanager
def project_transaction(
    connection: psycopg.Connection[Any], scope: ProjectScope
) -> Iterator[psycopg.Connection[Any]]:
    """Scope a trusted worker transaction. This is NOT end-user authorization.

    The caller must already be authorized for the project. Existing transactions
    are rejected so SET LOCAL settings cannot escape through a nested savepoint.
    """
    if connection.closed or connection.info.transaction_status != TransactionStatus.IDLE:
        raise StorageError("A dedicated idle connection is required for the project transaction.")
    try:
        with connection.transaction():
            connection.execute("SET LOCAL ROLE github_intelligence_runtime")
            connection.execute("SET LOCAL search_path = pg_catalog")
            with connection.cursor(row_factory=tuple_row) as cursor:
                flags = cursor.execute(
                    "SELECT r.rolsuper,r.rolbypassrls,r.rolcreatedb,r.rolcreaterole,"
                    "r.rolreplication,r.rolcanlogin,"
                    " pg_has_role(current_user,'ghi_owner','MEMBER'),"
                    " has_schema_privilege(current_user,'github_intelligence','CREATE'),"
                    " EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
                    " WHERE n.nspname='github_intelligence' AND c.relowner=r.oid)"
                    " FROM pg_roles r WHERE r.rolname=current_user"
                ).fetchone()
            if flags is None or any(flags):
                raise StorageError("Runtime role privileges are unsafe; project transaction refused.")
            connection.execute(
                "SELECT set_config('ghi.client_id',%s,true),set_config('ghi.project_id',%s,true)",
                (str(scope.client_id), str(scope.project_id)),
            )
            yield connection
    except psycopg.Error:
        # Do not expose DSNs, provider diagnostics, SQL, or parameter values.
        raise StorageError("Database operation failed; the project transaction was rolled back.") from None
