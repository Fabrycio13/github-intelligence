"""In-memory GitHub App authentication with strictly read-only scopes."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import jwt

from github_intelligence.github import API_ROOT, API_VERSION, GitHubError

_READ_PERMISSIONS = {name: "read" for name in ("contents", "pull_requests", "checks", "statuses", "metadata")}


class InsufficientInstallationPermissions(GitHubError):
    """The issued token is safe but lacks permissions required for onboarding."""


def installation_token(
    app_id: str,
    private_key: bytes,
    installation_id: int,
    *,
    repository_ids: list[int] | None = None,
    transport: httpx.BaseTransport | None = None,
    require_all_permissions: bool = False,
) -> str:
    """Exchange an app JWT for a validated read-only installation token.

    The exchange is deliberately not retried: it creates a token, unlike GET.
    No credential is persisted, logged, or included in an exception.
    """
    if type(installation_id) is not int or installation_id <= 0:
        raise GitHubError("Invalid GitHub installation ID")
    if repository_ids is not None:
        if (
            not isinstance(repository_ids, list)
            or not repository_ids
            or any(type(item) is not int or item <= 0 for item in repository_ids)
            or len(set(repository_ids)) != len(repository_ids)
        ):
            raise GitHubError("Repository IDs must be a nonempty list of unique positive integers")
    payload: dict[str, object] = {"permissions": dict(_READ_PERMISSIONS)}
    if repository_ids is not None:
        payload["repository_ids"] = list(repository_ids)
    app_jwt = create_app_jwt(app_id, private_key)
    try:
        with httpx.Client(transport=transport, timeout=30, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"{API_ROOT}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                },
                json=payload,
            )
    except (httpx.RequestError, ValueError):
        raise GitHubError("GitHub App token exchange failed") from None
    if response.status_code != 201:
        raise GitHubError("GitHub App token exchange failed", response.status_code)
    try:
        body = response.json()
    except (ValueError, UnicodeError):
        raise GitHubError("GitHub App returned invalid JSON", response.status_code) from None
    if not isinstance(body, dict):
        raise GitHubError("GitHub App returned an invalid token response", response.status_code)
    token = body.get("token")
    permissions = body.get("permissions")
    if (
        not isinstance(token, str)
        or not token
        or any(not 33 <= ord(c) <= 126 for c in token)
        or not isinstance(permissions, dict)
        or not permissions
        or any(name not in _READ_PERMISSIONS or value != "read" for name, value in permissions.items())
    ):
        raise GitHubError("GitHub App returned an invalid token or unsafe permissions", response.status_code)
    if require_all_permissions and permissions != _READ_PERMISSIONS:
        raise InsufficientInstallationPermissions("GitHub App installation permissions are incomplete")
    if repository_ids is not None:
        repositories = body.get("repositories")
        if (
            not isinstance(repositories, list)
            or any(not isinstance(repo, dict) or type(repo.get("id")) is not int for repo in repositories)
            or {repo["id"] for repo in repositories} != set(repository_ids)
            or len(repositories) != len(repository_ids)
        ):
            raise GitHubError("GitHub App token repository scope could not be verified", response.status_code)
    return token


def create_app_jwt(app_id: str, private_key: bytes, now: datetime | None = None) -> str:
    """Sign an RS256 JWT, backdating issuance to tolerate clock skew.

    Explicit times must be timezone-aware; naive times depend on local settings.
    Both numeric app IDs and alphanumeric GitHub client IDs are supported.
    """
    if not isinstance(app_id, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", app_id) is None:
        raise GitHubError("Invalid GitHub App ID")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise GitHubError("GitHub App JWT time must be timezone-aware")
    try:
        timestamp = int((now or datetime.now(UTC)).timestamp())
        return jwt.encode(
            {"iss": app_id, "iat": timestamp - 60, "exp": timestamp + 540}, private_key, algorithm="RS256"
        )
    except (ValueError, TypeError, jwt.PyJWTError):
        raise GitHubError("Unable to sign GitHub App JWT") from None
