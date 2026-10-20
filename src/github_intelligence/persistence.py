"""Persist one collected repository snapshot with scoped PostgreSQL history."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from .models import PullRequestSnapshot, ReportWindow, RepositorySnapshot
from .persistence_payload import snapshot_payload
from .storage import ProjectScope, StorageError, project_transaction

_SCHEMA = "github_intelligence"
_EVALUATOR_VERSION = "1"
_SHA = re.compile(r"^[a-fA-F0-9]{7,64}$")


@dataclass(frozen=True)
class PersistedSync:
    sync_run_id: UUID
    report_id: UUID
    snapshot_ids: tuple[UUID, ...]
    status: str


def _audit(
    connection: psycopg.Connection[Any],
    scope: ProjectScope,
    action: str,
    entity_id: UUID,
    actor_kind: str,
) -> None:
    connection.execute(
        f"INSERT INTO {_SCHEMA}.audit_events"
        "(client_id,project_id,action,entity_id,actor_kind) VALUES(%s,%s,%s,%s,%s)",
        (scope.client_id, scope.project_id, action, entity_id, actor_kind),
    )


def _validate_snapshot(
    repository: RepositorySnapshot,
) -> list[tuple[PullRequestSnapshot, dict[str, Any], str]]:
    configured_id = repository.config.github_id
    if configured_id is None or repository.github_id != configured_id:
        raise StorageError("Repository identity is missing or does not match the collected snapshot.")
    payloads: list[tuple[PullRequestSnapshot, dict[str, Any], str]] = []
    numbers: set[int] = set()
    for snapshot in repository.pulls:
        if snapshot.repository.casefold() != repository.config.full_name.casefold():
            raise StorageError("Collected pull request belongs to a different repository.")
        number = snapshot.pull.get("number")
        head = snapshot.pull.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if type(number) is not int or number < 1 or not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
            raise StorageError("Collected pull request identity is invalid.")
        if number in numbers:
            raise StorageError("Collected snapshot contains a duplicate pull request.")
        numbers.add(number)
        payload = snapshot_payload(snapshot)
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        payloads.append((snapshot, payload, fingerprint))
    return sorted(payloads, key=lambda item: int(item[0].pull["number"]))


def persist_repository_snapshot(
    connection: psycopg.Connection[Any],
    scope: ProjectScope,
    repository: RepositorySnapshot,
    window: ReportWindow,
    report_content: str,
    *,
    github_app_id: int,
    github_installation_id: int,
) -> PersistedSync:
    """Persist a new observation; require pre-provisioned active project/repository rows.

    Collection and report rendering happen before this call. Each invocation creates
    a distinct immutable sync run and report; it never updates or deletes history.
    A persistence failure rolls back snapshots/report and records the run as failed
    when the database remains reachable.
    """
    if not isinstance(report_content, str) or not report_content.strip():
        raise StorageError("A nonempty rendered report is required.")
    if type(github_app_id) is not int or github_app_id <= 0:
        raise StorageError("A valid GitHub App ID is required.")
    if type(github_installation_id) is not int or github_installation_id <= 0:
        raise StorageError("A valid GitHub installation ID is required.")
    payloads = _validate_snapshot(repository)
    partial = (
        not repository.complete
        or bool(repository.limitations)
        or any(not item.consistent or item.limitations for item, _, _ in payloads)
    )
    final_status = "partial" if partial else "succeeded"
    actor = "manual_cli"

    with project_transaction(connection, scope):
        row = connection.execute(
            f"SELECT r.id,r.status,i.status,i.github_app_id,i.github_installation_id "
            f"FROM {_SCHEMA}.repositories r"
            f" JOIN {_SCHEMA}.github_installations i"
            " ON i.client_id=r.client_id AND i.id=r.installation_id"
            " WHERE r.client_id=%s AND r.project_id=%s AND r.github_repository_id=%s"
            " AND lower(r.full_name)=lower(%s)",
            (scope.client_id, scope.project_id, repository.github_id, repository.config.full_name),
        ).fetchone()
        if (
            row is None
            or row[1] != "active"
            or row[2] != "active"
            or row[3] != github_app_id
            or row[4] != github_installation_id
        ):
            raise StorageError("Repository is not pre-provisioned and active in the requested project.")
        repository_id = row[0]
        run_row = connection.execute(
            f"INSERT INTO {_SCHEMA}.sync_runs"
            "(client_id,project_id,repository_id,window_start,window_end)"
            " VALUES(%s,%s,%s,%s,%s) RETURNING id",
            (scope.client_id, scope.project_id, repository_id, window.since, window.until),
        ).fetchone()
        if run_row is None or not isinstance(run_row[0], UUID):
            raise StorageError("Database did not return the sync run identity.")
        run_id = run_row[0]
        connection.execute(
            f"UPDATE {_SCHEMA}.sync_runs SET status='running',started_at=clock_timestamp() WHERE id=%s",
            (run_id,),
        )
        _audit(connection, scope, "sync_queued", run_id, actor)
        _audit(connection, scope, "sync_started", run_id, actor)

    try:
        with project_transaction(connection, scope):
            snapshot_ids: list[UUID] = []
            for snapshot, payload, fingerprint in payloads:
                snapshot_row = connection.execute(
                    f"INSERT INTO {_SCHEMA}.pr_snapshots"
                    "(client_id,project_id,repository_id,sync_run_id,pr_number,head_sha,observed_at,"
                    "evaluator_version,payload,fingerprint)"
                    " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        scope.client_id,
                        scope.project_id,
                        repository_id,
                        run_id,
                        snapshot.pull["number"],
                        snapshot.pull["head"]["sha"],
                        snapshot.observed_at,
                        _EVALUATOR_VERSION,
                        Jsonb(payload),
                        fingerprint,
                    ),
                ).fetchone()
                if snapshot_row is None or not isinstance(snapshot_row[0], UUID):
                    raise StorageError("Database did not return the snapshot identity.")
                snapshot_id = snapshot_row[0]
                snapshot_ids.append(snapshot_id)
                _audit(connection, scope, "snapshot_saved", snapshot_id, actor)
            report_row = connection.execute(
                f"INSERT INTO {_SCHEMA}.reports"
                "(client_id,project_id,window_start,window_end,content,complete)"
                " VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    scope.client_id,
                    scope.project_id,
                    window.since,
                    window.until,
                    report_content,
                    not partial,
                ),
            ).fetchone()
            if report_row is None or not isinstance(report_row[0], UUID):
                raise StorageError("Database did not return the report identity.")
            report_id = report_row[0]
            sources: list[UUID | None] = list(snapshot_ids) if snapshot_ids else [None]
            for source_snapshot_id in sources:
                connection.execute(
                    f"INSERT INTO {_SCHEMA}.report_sources"
                    "(client_id,project_id,report_id,sync_run_id,snapshot_id) VALUES(%s,%s,%s,%s,%s)",
                    (scope.client_id, scope.project_id, report_id, run_id, source_snapshot_id),
                )
            connection.execute(
                f"UPDATE {_SCHEMA}.sync_runs SET status=%s,finished_at=clock_timestamp() WHERE id=%s",
                (final_status, run_id),
            )
            _audit(connection, scope, "report_created", report_id, actor)
            _audit(connection, scope, "sync_finished", run_id, actor)
    except (StorageError, psycopg.Error):
        try:
            with project_transaction(connection, scope):
                updated = connection.execute(
                    f"UPDATE {_SCHEMA}.sync_runs SET status='failed',finished_at=clock_timestamp(),"
                    "error_code='PERSISTENCE_FAILED' WHERE id=%s AND status='running'",
                    (run_id,),
                )
                if updated.rowcount != 1:
                    raise StorageError("Failed sync run could not be finalized.")
                _audit(connection, scope, "sync_finished", run_id, actor)
        except (StorageError, psycopg.Error):
            raise StorageError("Persistence failed and the sync run could not be finalized.") from None
        raise StorageError("Persistence failed; the sync run was marked failed.") from None

    return PersistedSync(
        sync_run_id=run_id,
        report_id=report_id,
        snapshot_ids=tuple(snapshot_ids),
        status=final_status,
    )
