"""Read-only dashboard data for an already-authorized project scope."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from github_intelligence.storage import ProjectScope, project_transaction

# Reports are selected before sources: multiple snapshots never multiply totals.
_HISTORY_CTE = """
WITH scoped_reports AS (
    SELECT report.* FROM github_intelligence.reports report
    JOIN github_intelligence.projects p
      ON (p.client_id,p.id)=(report.client_id,report.project_id)
    JOIN github_intelligence.clients c ON c.id=report.client_id
    WHERE report.client_id=%s AND report.project_id=%s
      AND c.status='active' AND p.status='active'
), report_repositories AS (
    SELECT DISTINCT report.id AS report_id,r.id,r.full_name
    FROM scoped_reports report
    JOIN github_intelligence.report_sources source
      ON (source.client_id,source.project_id,source.report_id)
       =(report.client_id,report.project_id,report.id)
    JOIN github_intelligence.sync_runs s
      ON (s.client_id,s.project_id,s.id)=(source.client_id,source.project_id,source.sync_run_id)
    JOIN github_intelligence.repositories r
      ON (r.client_id,r.project_id,r.id)=(s.client_id,s.project_id,s.repository_id)
), summaries AS (
    SELECT report.id,report.complete,report.generated_at,report.window_start,report.window_end,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('id',r.id,'full_name',r.full_name)
                               ORDER BY r.full_name,r.id)
                FROM report_repositories r WHERE r.report_id=report.id),'[]'::jsonb) AS repositories
    FROM scoped_reports report
)
"""


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

    def list_reports(
        self, *, repository_id: UUID | None = None, limit: int = 20, offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
            raise ValueError("Invalid report pagination")
        with self._connection_factory() as connection, project_transaction(connection, self._scope):
            connection.execute("SET LOCAL TIME ZONE 'UTC'")
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    _HISTORY_CTE + """, filtered AS (
                        SELECT * FROM summaries summary
                        WHERE %s::uuid IS NULL OR EXISTS (
                            SELECT 1 FROM report_repositories r
                            WHERE r.report_id=summary.id AND r.id=%s)
                    ), page AS (
                        SELECT * FROM filtered ORDER BY generated_at DESC,id DESC LIMIT %s OFFSET %s
                    ), options AS (
                        SELECT DISTINCT id,full_name FROM report_repositories
                    ) SELECT (SELECT count(*) FROM filtered) AS total,
                        COALESCE((SELECT jsonb_agg(to_jsonb(options) ORDER BY full_name,id)
                                  FROM options),'[]'::jsonb) AS repository_options,
                        COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY generated_at DESC,id DESC)
                                  FROM page),'[]'::jsonb) AS reports""",
                    (self._scope.client_id, self._scope.project_id,
                     repository_id, repository_id, limit, offset),
                ).fetchone()
                assert row is not None
                return {**row, "limit": limit, "offset": offset}

    def get_dashboard(self) -> dict[str, Any]:
        # All aggregates share one PostgreSQL statement snapshot (including the UTC date).
        with self._connection_factory() as connection, project_transaction(connection, self._scope):
            connection.execute("SET LOCAL TIME ZONE 'UTC'")
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    _HISTORY_CTE + """, period AS (
                        SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date AS today
                    ), daily AS (
                        SELECT today-n AS date,
                          (SELECT count(*) FROM scoped_reports report
                           WHERE report.generated_at >= (today-n)::timestamp AT TIME ZONE 'UTC'
                             AND report.generated_at < (today-n+1)::timestamp AT TIME ZONE 'UTC') AS count
                        FROM period CROSS JOIN generate_series(0,29) n
                    ), distribution AS (
                        SELECT id,full_name,count(*) AS count FROM report_repositories GROUP BY id,full_name
                    ), recent AS (
                        SELECT * FROM summaries ORDER BY generated_at DESC,id DESC LIMIT 5
                    ) SELECT
                        (SELECT count(*) FROM github_intelligence.repositories r
                         JOIN github_intelligence.github_installations i
                           ON (i.client_id,i.id)=(r.client_id,r.installation_id)
                         JOIN github_intelligence.projects p ON (p.client_id,p.id)=(r.client_id,r.project_id)
                         JOIN github_intelligence.clients c ON c.id=r.client_id
                         WHERE r.client_id=%s AND r.project_id=%s AND r.status='active'
                           AND i.status='active' AND p.status='active' AND c.status='active')
                           AS connected_repositories,
                        (SELECT count(*) FROM scoped_reports) AS total_reports,
                        (SELECT count(*) FROM scoped_reports WHERE NOT complete) AS partial_reports,
                        (SELECT jsonb_agg(to_jsonb(daily) ORDER BY date) FROM daily) AS daily_reports,
                        COALESCE((SELECT jsonb_agg(to_jsonb(distribution) ORDER BY full_name,id)
                                  FROM distribution),'[]'::jsonb) AS repository_reports,
                        COALESCE((SELECT jsonb_agg(to_jsonb(recent) ORDER BY generated_at DESC,id DESC)
                                  FROM recent),'[]'::jsonb) AS recent_reports,
                        (SELECT (today-29)::text FROM period) AS period_start,
                        (SELECT today::text FROM period) AS period_end""",
                    (self._scope.client_id, self._scope.project_id,
                     self._scope.client_id, self._scope.project_id),
                ).fetchone()
                assert row is not None
                return row

    def get_report_details(self, report_id: UUID) -> dict[str, Any] | None:
        with self._connection_factory() as connection, project_transaction(connection, self._scope):
            connection.execute("SET LOCAL TIME ZONE 'UTC'")
            with connection.cursor(row_factory=dict_row) as cursor:
                row = cursor.execute(
                    _HISTORY_CTE + """
                        SELECT to_jsonb(summary) || jsonb_build_object('content',report.content)
                        AS detail FROM summaries summary JOIN scoped_reports report ON report.id=summary.id
                        WHERE summary.id=%s""",
                    (self._scope.client_id, self._scope.project_id, report_id),
                ).fetchone()
                return None if row is None else row["detail"]

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
