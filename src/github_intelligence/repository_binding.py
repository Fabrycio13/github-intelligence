"""Insert-only repository onboarding for an already-authorized trusted worker.

Only pass a fresh, ready RepositoryAccess.inspect result from the provider API;
this persistence boundary does not authenticate users or verify GitHub access.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from github_intelligence.models import RepositoryConfig
from github_intelligence.storage import ProjectScope, StorageError, project_transaction

ONBOARDING_PERMISSION_REQUIRED = "ONBOARDING_PERMISSION_REQUIRED"
_INSERT_COLUMNS = {
    "github_installations": ("client_id", "github_app_id", "github_installation_id", "status", "verified_at"),
    "repositories": (
        "client_id",
        "project_id",
        "installation_id",
        "github_repository_id",
        "full_name",
        "status",
        "main_branch",
        "expected_approvals",
    ),
}


def _has_insert_columns(cursor: psycopg.Cursor[Any]) -> bool:
    has_columns = all(
        cursor.execute(
            "SELECT has_column_privilege(current_user,%s,%s,'INSERT')",
            (f"github_intelligence.{table}", column),
        ).fetchone()
        == (True,)
        for table, columns in _INSERT_COLUMNS.items()
        for column in columns
    )
    if not has_columns:
        return False
    return cursor.execute(
        "SELECT has_function_privilege(current_user,"
        " to_regprocedure('github_intelligence.lock_repository_binding_scope(bigint,bigint)'),'EXECUTE')"
    ).fetchone() == (True,)


def _active_scope(cursor: psycopg.Cursor[Any], scope: ProjectScope) -> bool:
    return cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM github_intelligence.clients c"
        " JOIN github_intelligence.projects p ON p.client_id=c.id"
        " WHERE c.id=%s AND p.id=%s AND c.status='active' AND p.status='active')",
        (scope.client_id, scope.project_id),
    ).fetchone() == (True,)


def _validate(verified: dict[str, Any]) -> None:
    required = {"status", "full_name", "github_id", "app_id", "installation_id", "main_branch"}
    if (
        not isinstance(verified, dict)
        or not required.issubset(verified)
        or verified["status"] != "ready"
        or any(
            type(verified[key]) is not int or verified[key] <= 0
            for key in ("github_id", "app_id", "installation_id")
        )
        or not isinstance(verified["full_name"], str)
        or (verified["main_branch"] is not None and not isinstance(verified["main_branch"], str))
    ):
        raise StorageError("Invalid verified repository result.")
    try:
        RepositoryConfig(full_name=verified["full_name"])
    except ValueError:
        raise StorageError("Invalid verified repository result.") from None


class RepositoryBinder:
    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[psycopg.Connection[Any]]],
        scope: ProjectScope,
    ) -> None:
        self._connection_factory = connection_factory
        self._scope = scope

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with self._connection_factory() as connection, project_transaction(connection, self._scope):
                yield connection
        except psycopg.Error:
            raise StorageError("Repository binding database operation failed; details omitted.") from None

    def supported(self) -> bool:
        """Read-only capability probe under runtime RLS and the trusted project scope."""
        with self._transaction() as connection:
            with connection.cursor(row_factory=tuple_row) as cursor:
                return _active_scope(cursor, self._scope) and _has_insert_columns(cursor)

    def bind(self, verified: dict[str, Any]) -> dict[str, Any]:
        _validate(verified)
        with self._transaction() as connection:
            with connection.cursor(row_factory=tuple_row) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"repository_binding:{self._scope.client_id}",),
                )
                if not _active_scope(cursor, self._scope):
                    raise StorageError("Active client and project are required for repository binding.")
                if not _has_insert_columns(cursor):
                    raise StorageError(ONBOARDING_PERMISSION_REQUIRED)
                locked = cursor.execute(
                    "SELECT github_intelligence.lock_repository_binding_scope(%s::bigint,%s::bigint)",
                    (verified["app_id"], verified["installation_id"]),
                ).fetchone()
                if locked != (True,):
                    raise StorageError("Active client and project are required for repository binding.")
                installation = cursor.execute(
                    "SELECT id,status FROM github_intelligence.github_installations"
                    " WHERE client_id=%s AND github_app_id=%s AND github_installation_id=%s",
                    (self._scope.client_id, verified["app_id"], verified["installation_id"]),
                ).fetchone()
                if installation is None:
                    installation = cursor.execute(
                        "INSERT INTO github_intelligence.github_installations"
                        "(client_id,github_app_id,github_installation_id,status,verified_at)"
                        " VALUES(%s,%s,%s,'active',clock_timestamp()) RETURNING id,status",
                        (self._scope.client_id, verified["app_id"], verified["installation_id"]),
                    ).fetchone()
                if installation is None or installation[1] != "active":
                    raise StorageError("Repository binding conflicts with existing configuration.")
                rows = cursor.execute(
                    "SELECT id,full_name,status,github_repository_id,installation_id"
                    " FROM github_intelligence.repositories WHERE client_id=%s AND project_id=%s"
                    " AND (github_repository_id=%s OR lower(full_name)=lower(%s))",
                    (
                        self._scope.client_id,
                        self._scope.project_id,
                        verified["github_id"],
                        verified["full_name"],
                    ),
                ).fetchall()
                created = not rows
                repository: tuple[Any, ...] | None
                if rows:
                    if (
                        len(rows) != 1
                        or rows[0][1] != verified["full_name"]
                        or rows[0][2] != "active"
                        or rows[0][3] != verified["github_id"]
                        or rows[0][4] != installation[0]
                    ):
                        raise StorageError("Repository binding conflicts with existing configuration.")
                    repository = rows[0]
                else:
                    repository = cursor.execute(
                        "INSERT INTO github_intelligence.repositories"
                        "(client_id,project_id,installation_id,github_repository_id,full_name,"
                        "status,main_branch,expected_approvals)"
                        " VALUES(%s,%s,%s,%s,%s,'active',%s,2) RETURNING id,full_name,status",
                        (
                            self._scope.client_id,
                            self._scope.project_id,
                            installation[0],
                            verified["github_id"],
                            verified["full_name"],
                            verified["main_branch"],
                        ),
                    ).fetchone()
                if repository is None:
                    raise StorageError("Repository binding failed.")
                return {
                    "id": str(repository[0]),
                    "full_name": repository[1],
                    "status": "active",
                    "created": created,
                    "installation_id": verified["installation_id"],
                }
