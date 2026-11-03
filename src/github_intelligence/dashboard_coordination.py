"""Coordination and concurrency control for the loopback dashboard."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from github_intelligence.models import ReportWindow

__all__ = [
    "CoordinationStore",
    "DashboardCoordinator",
    "DashboardError",
    "OnboardingService",
    "validate_report_window",
    "validate_update_payload",
]


class CoordinationStore(Protocol):
    def list_repositories(self) -> list[dict[str, Any]]: ...


class OnboardingService(Protocol):
    def bind(self, payload: object) -> dict[str, Any]: ...


class DashboardError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def validate_report_window(payload: object) -> tuple[str, ReportWindow]:
    if not isinstance(payload, dict) or set(payload) != {"repository_id", "since", "until"}:
        raise DashboardError(400, "Informe repositório e intervalo com timezone.")
    try:
        repository_id = str(UUID(payload["repository_id"]))
        window = ReportWindow(since=payload["since"], until=payload["until"])
    except (ValueError, TypeError, ValidationError, AttributeError):
        raise DashboardError(400, "Repositório ou intervalo inválido.") from None
    if window.until > datetime.now(timezone.utc):
        raise DashboardError(400, "O fim do intervalo não pode estar no futuro.")
    return repository_id, window


validate_update_payload = validate_report_window


class DashboardCoordinator:
    """Coordinates concurrency and validation for the local dashboard."""

    def __init__(self, lock: Lock | None = None) -> None:
        self.lock = lock if lock is not None else Lock()

    def locked(self) -> bool:
        return self.lock.locked()

    @contextmanager
    def hold(self, conflict_message: str) -> Iterator[None]:
        if not self.lock.acquire(blocking=False):
            raise DashboardError(409, conflict_message)
        try:
            yield
        finally:
            self.lock.release()

    def coordinate_update(
        self,
        store: CoordinationStore,
        refresh: Callable[[str, ReportWindow], None],
        payload: object,
    ) -> None:
        repository_id, window = validate_report_window(payload)
        with self.hold("Uma coleta já está em andamento. Aguarde."):
            repo = next((r for r in store.list_repositories() if r["id"] == repository_id), None)
            if repo is None:
                raise DashboardError(404, "Repositório não encontrado neste projeto.")
            if repo["status"] != "active" or repo["installation_status"] != "active":
                raise DashboardError(409, "Repositório ou instalação não está ativo.")
            if repo.get("last_status") in {"pending", "running"}:
                raise DashboardError(409, "Este repositório já tem uma coleta em andamento.")
            refresh(repo["full_name"], window)

    def coordinate_bind(
        self,
        onboarding: OnboardingService,
        payload: object,
    ) -> dict[str, Any]:
        with self.hold("Outra operação está em andamento. Aguarde."):
            return onboarding.bind(payload)
