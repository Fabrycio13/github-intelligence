"""Inspect GitHub App repository access without persisting credentials."""

import re
from typing import Any

import httpx

from github_intelligence.auth import (
    InsufficientInstallationPermissions,
    create_app_jwt,
    installation_token,
)
from github_intelligence.config import AppSettings
from github_intelligence.github import GitHubClient, GitHubError
from github_intelligence.models import RepositoryConfig


class RepositoryAccess:
    """Resolve the repository's actual installation; settings are signing credentials only."""

    def __init__(self, settings: AppSettings, *, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def inspect(self, link: str) -> dict[str, Any]:
        full_name = parse_repository_link(link)
        settings = self._settings
        jwt = create_app_jwt(settings.app_id, settings.private_key)
        with GitHubClient(jwt, transport=self._transport, max_attempts=1) as client:
            app = client.get("/app")
            if (
                not isinstance(app, dict)
                or type(app.get("id")) is not int
                or app["id"] <= 0
                or str(app["id"]) != settings.app_id
                or not isinstance(app.get("slug"), str)
                or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", app["slug"]) is None
            ):
                raise GitHubError("GitHub returned an invalid App identity")
            try:
                installation = client.get(f"/repos/{full_name}/installation")
            except GitHubError as exc:
                if exc.status_code != 404:
                    raise
                return {
                    "status": "authorization_required", "full_name": full_name,
                    "authorization_url": f"https://github.com/apps/{app['slug']}/installations/new",
                    "message": "Autorize a GitHub App neste repositório e depois verifique novamente.",
                }
        if (
            not isinstance(installation, dict)
            or type(installation.get("id")) is not int
            or installation["id"] <= 0
            or type(installation.get("app_id")) is not int
            or installation["app_id"] != app["id"]
            or "suspended_at" not in installation
            or installation["suspended_at"] is not None
        ):
            raise GitHubError("GitHub returned an invalid or suspended App installation")
        try:
            token = installation_token(
                settings.app_id, settings.private_key, installation["id"], transport=self._transport,
                require_all_permissions=True,
            )
        except InsufficientInstallationPermissions:
            return {
                "status": "authorization_required", "full_name": full_name,
                "authorization_url": f"https://github.com/apps/{app['slug']}/installations/new",
                "message": "A instalação precisa aprovar as permissões de leitura da GitHub App. "
                           "Confira a autorização no GitHub e verifique novamente.",
            }
        with GitHubClient(token, transport=self._transport, max_attempts=1) as client:
            repository = client.get(f"/repos/{full_name}")
        if (
            not isinstance(repository, dict)
            or type(repository.get("id")) is not int
            or repository["id"] <= 0
            or not isinstance(repository.get("full_name"), str)
            or repository["full_name"].casefold() != full_name.casefold()
            or (repository.get("default_branch") is not None
                and not isinstance(repository["default_branch"], str))
        ):
            raise GitHubError("GitHub returned an invalid or changed repository identity")
        return {
            "status": "ready", "full_name": repository["full_name"], "github_id": repository["id"],
            "app_id": app["id"], "installation_id": installation["id"],
            "main_branch": repository.get("default_branch"),
        }


def parse_repository_link(link: str) -> str:
    """Accept literal OWNER/REPO or an exact HTTPS github.com repository URL."""
    try:
        if not isinstance(link, str) or any(c.isspace() or ord(c) < 33 for c in link):
            raise ValueError
        if any(c in link for c in "\\%?#"):
            raise ValueError
        name = link
        if link.startswith("https://github.com/"):
            name = link.removeprefix("https://github.com/").removesuffix("/")
            # Validate before stripping the clone suffix: never repair dot segments.
            RepositoryConfig(full_name=name)
            name = name.removesuffix(".git")
        return RepositoryConfig(full_name=name).full_name
    except ValueError:
        raise ValueError("Expected literal OWNER/REPO or an HTTPS GitHub repository URL") from None
