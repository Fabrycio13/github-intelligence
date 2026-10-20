"""Local CLI selection, scoped by client/project. Not a multi-user auth store."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import Field, ValidationError

from github_intelligence.models import Contract, RepositoryConfig


class RegistryError(Exception):
    """Safe local selection failure."""


class RegistryDocument(Contract):
    version: int = 1
    repositories: list[RepositoryConfig] = Field(default_factory=list)


class RepositoryRegistry:
    def __init__(self, path: Path):
        self.path = path

    def entries(self) -> list[RepositoryConfig]:
        if not self.path.exists():
            return []
        try:
            document = RegistryDocument.model_validate_json(self.path.read_text(encoding="utf-8"))
            if document.version != 1:
                raise RegistryError("Versão de configuração não suportada.")
            seen = set()
            for repo in document.repositories:
                key = (repo.client_id, repo.project_id, repo.full_name.casefold())
                if key in seen:
                    raise RegistryError("Configuração possui repositórios duplicados no mesmo projeto.")
                seen.add(key)
            return document.repositories
        except (OSError, ValueError, ValidationError) as from_error:
            raise RegistryError(
                "Não foi possível ler a configuração local; nenhum dado foi sobrescrito."
            ) from from_error

    def active(
        self, *, client_id: str | None = None, project_id: str | None = None
    ) -> list[RepositoryConfig]:
        return [
            repo
            for repo in self.entries()
            if repo.status == "active"
            and (client_id is None or repo.client_id == client_id)
            and (project_id is None or repo.project_id == project_id)
        ]

    def _save(self, entries: list[RepositoryConfig]) -> None:
        # The adjacent pending file is a runtime write transaction, not a scratch probe.
        # Exclusive lock rejects concurrent CLI writers instead of losing a selection.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_name(self.path.name + ".pending")
        try:
            with pending.open("x", encoding="utf-8") as handle:
                handle.write(RegistryDocument(repositories=entries).model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise RegistryError("Outra gravação está em andamento; configuração não alterada.") from exc
        try:
            os.replace(pending, self.path)
        finally:
            pending.unlink(missing_ok=True)

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            handle = lock.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise RegistryError("Outra gravação está em andamento; configuração não alterada.") from exc
        try:
            with handle:
                yield
        finally:
            lock.unlink(missing_ok=True)

    def add(self, entry: RepositoryConfig) -> None:
        with self._write_lock():
            entries = self.entries()
            for index, existing in enumerate(entries):
                same_scope = (existing.client_id, existing.project_id) == (entry.client_id, entry.project_id)
                same_name = existing.full_name.casefold() == entry.full_name.casefold()
                same_id = existing.github_id is not None and existing.github_id == entry.github_id
                if same_scope and (same_name or same_id):
                    if same_name and existing.github_id and entry.github_id and not same_id:
                        raise RegistryError(
                            "O nome agora identifica outro repositório; revisão manual necessária."
                        )
                    entries[index] = entry.model_copy(update={
                        "status": "active",
                        "github_installation_id": (
                            entry.github_installation_id
                            if entry.github_installation_id is not None else existing.github_installation_id
                        ),
                    })
                    self._save(entries)
                    return
            entries.append(entry.model_copy(update={"status": "active"}))
            self._save(entries)

    def link_verified(self, entry: RepositoryConfig) -> None:
        """Persist an onboarding identity without resetting settings or reactivating rows."""
        if entry.github_id is None or entry.github_installation_id is None or entry.status != "active":
            raise RegistryError("O vínculo exige identidade e instalação verificadas.")
        with self._write_lock():
            entries = self.entries()
            for index, existing in enumerate(entries):
                if (existing.client_id, existing.project_id) != (entry.client_id, entry.project_id):
                    continue
                if (
                    existing.full_name.casefold() != entry.full_name.casefold()
                    and existing.github_id != entry.github_id
                ):
                    continue
                if (
                    existing.status != "active"
                    or existing.full_name.casefold() != entry.full_name.casefold()
                    or existing.github_id not in (None, entry.github_id)
                    or existing.github_installation_id not in (None, entry.github_installation_id)
                ):
                    raise RegistryError("Vínculo local conflitante ou desativado; revisão necessária.")
                entries[index] = existing.model_copy(update={
                    "github_id": entry.github_id, "github_installation_id": entry.github_installation_id,
                })
                self._save(entries)
                return
            entries.append(entry)
            self._save(entries)

    def set_status(self, name: str, status: str, *, client_id: str, project_id: str) -> None:
        RepositoryConfig(full_name=name, status=status)
        with self._write_lock():
            entries = self.entries()
            for index, repo in enumerate(entries):
                if (repo.client_id, repo.project_id, repo.full_name.casefold()) == (
                    client_id,
                    project_id,
                    name.casefold(),
                ):
                    entries[index] = repo.model_copy(update={"status": status})
                    self._save(entries)
                    return
            raise RegistryError("Repositório não encontrado no cliente/projeto informado.")
