"""Read-only dashboard data for an already-authorized project scope."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from github_intelligence.storage import ProjectScope, project_transaction


class DashboardStore:
    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[psycopg.Connection[Any]]],
        scope: ProjectScope,
    ) -> None:
        self._connection_factory = connection_factory
        self._scope = scope

    def list_repositories(self) -> list[dict[str, Any]]:
        with self._connection_factory() as connection, project_transaction(connection, self._scope):
            with connection.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    "SELECT r.id::text AS id,r.full_name,r.status,i.status AS installation_status,"
                    " s.status AS last_status,s.started_at AS last_started_at,"
                    " s.finished_at AS last_finished_at,s.window_start,s.window_end,"
                    " report.id::text AS report_id,report.complete AS report_complete"
                    " FROM github_intelligence.repositories r"
                    " JOIN github_intelligence.projects p ON (p.client_id,p.id)=(r.client_id,r.project_id)"
                    " JOIN github_intelligence.clients c ON c.id=r.client_id"
                    " JOIN github_intelligence.github_installations i"
                    " ON (i.client_id,i.id)=(r.client_id,r.installation_id)"
                    " LEFT JOIN LATERAL ("
                    " SELECT s.* FROM github_intelligence.sync_runs s"
                    " WHERE (s.client_id,s.project_id,s.repository_id)=(r.client_id,r.project_id,r.id)"
                    " ORDER BY s.created_at DESC,s.id DESC LIMIT 1) s ON true"
                    " LEFT JOIN LATERAL ("
                    " SELECT report.id,report.complete FROM github_intelligence.reports report"
                    " WHERE (report.client_id,report.project_id)=(r.client_id,r.project_id)"
                    " AND EXISTS (SELECT 1 FROM github_intelligence.report_sources source"
                    " WHERE (source.client_id,source.project_id,source.report_id,source.sync_run_id)"
                    " =(r.client_id,r.project_id,report.id,s.id))"
                    " ORDER BY report.generated_at DESC,report.id DESC LIMIT 1) report ON true"
                    " WHERE r.client_id=%s AND r.project_id=%s AND r.status<>'removed'"
                    " AND c.status='active' AND p.status='active' ORDER BY r.full_name,r.id",
                    (self._scope.client_id, self._scope.project_id),
                ).fetchall()
                return [_json_row(row) for row in rows]

    def get_report(self, report_id: UUID) -> dict[str, Any] | None:
        with self._connection_factory() as connection, project_transaction(connection, self._scope):
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    "SELECT report.id::text AS id,report.content,report.complete,report.generated_at"
                    " FROM github_intelligence.reports report"
                    " JOIN github_intelligence.projects p"
                    " ON (p.client_id,p.id)=(report.client_id,report.project_id)"
                    " JOIN github_intelligence.clients c ON c.id=report.client_id"
                    " WHERE report.id=%s AND report.client_id=%s AND report.project_id=%s"
                    " AND c.status='active' AND p.status='active'",
                    (report_id, self._scope.client_id, self._scope.project_id),
                ).fetchone()
                return None if row is None else _json_row(row)


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }
