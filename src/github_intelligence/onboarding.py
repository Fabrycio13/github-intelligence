"""Explicit link inspection and binding; never starts a collection."""
from typing import Any, Protocol

from github_intelligence.models import RepositoryConfig
from github_intelligence.repositories import RegistryError, RepositoryRegistry
from github_intelligence.storage import StorageError


class Access(Protocol):
    def inspect(self, link: str) -> dict[str, Any]: ...


class Binder(Protocol):
    def supported(self) -> bool: ...
    def bind(self, verified: dict[str, Any]) -> dict[str, Any]: ...


class OnboardingError(StorageError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class RepositoryOnboarding:
    def __init__(
        self, access: Access, binder: Binder, registry: RepositoryRegistry, *, client: str, project: str,
    ) -> None:
        self.access = access
        self.binder = binder
        self.registry = registry
        self.client = client
        self.project = project

    def inspect(self, payload: object) -> dict[str, Any]:
        if (
            not isinstance(payload, dict) or set(payload) != {"link"}
            or not isinstance(payload["link"], str) or not 0 < len(payload["link"]) <= 512
        ):
            raise OnboardingError(400, "Informe apenas o link HTTPS do repositório ou OWNER/REPO.")
        try:
            result = self.access.inspect(payload["link"])
        except ValueError:
            raise OnboardingError(
                400, "Use um link HTTPS do GitHub ou OWNER/REPO, sem parâmetros extras."
            ) from None
        if result["status"] == "ready":
            self._check_local(result)
            result = {**result, "can_bind": self.binder.supported()}
            if not result["can_bind"]:
                result["message"] = (
                    "Acesso GitHub confirmado. "
                    "A liberação única do cadastro no banco ainda precisa ser aplicada."
                )
        return result

    def _check_local(self, verified: dict[str, Any]) -> None:
        for entry in self.registry.entries():
            if (entry.client_id, entry.project_id) != (self.client, self.project):
                continue
            if (entry.full_name.casefold() != verified["full_name"].casefold()
                    and entry.github_id != verified["github_id"]):
                continue
            if (
                entry.status != "active" or entry.full_name.casefold() != verified["full_name"].casefold()
                or entry.github_id not in (None, verified["github_id"])
                or entry.github_installation_id not in (None, verified["installation_id"])
            ):
                raise OnboardingError(409, "Vínculo local conflitante ou desativado; não será sobrescrito.")

    def bind(self, payload: object) -> dict[str, Any]:
        # Browser IDs are comparison-only: the fresh provider response is the authority.
        identity_keys = {"full_name", "github_id", "app_id", "installation_id"}
        if (
            not isinstance(payload, dict) or set(payload) != {"link", "expected"}
            or not isinstance(payload["expected"], dict)
            or set(payload["expected"]) != identity_keys
            or not isinstance(payload["expected"]["full_name"], str)
            or any(type(payload["expected"][key]) is not int or payload["expected"][key] <= 0
                   for key in ("github_id", "app_id", "installation_id"))
        ):
            raise OnboardingError(
                400, "Verifique o acesso e confirme a identidade exibida antes de vincular.",
            )
        verified = self.inspect({"link": payload["link"]})
        if verified["status"] != "ready":
            return verified
        if any(verified[key] != payload["expected"][key] for key in identity_keys):
            raise OnboardingError(
                409, "O repositório ou a instalação mudou após a verificação. "
                     "Verifique novamente e confirme a nova identidade.",
            )
        if not verified["can_bind"]:
            raise OnboardingError(
                503, "ONBOARDING_PERMISSION_REQUIRED: cadastro ainda não liberado no banco."
            )
        result = self.binder.bind({
            key: verified[key]
            for key in ("status", "full_name", "github_id", "app_id", "installation_id", "main_branch")
        })
        try:
            self.registry.link_verified(RepositoryConfig(
                full_name=verified["full_name"], github_id=verified["github_id"],
                github_installation_id=verified["installation_id"],
                client_id=self.client, project_id=self.project,
            ))
        except (RegistryError, OSError):
            raise OnboardingError(
                503, "Vínculo salvo no banco, mas a seleção local não foi salva. "
                "Confirme novamente para reparar."
            ) from None
        return {
            "status": "bound", "repository": result,
            "message": "Repositório vinculado. Nenhuma coleta foi iniciada; use Atualizar agora.",
        }
