"""Shared test helpers and synthetic fixtures for onboarding and repository access."""
from pathlib import Path

from github_intelligence.repositories import RepositoryRegistry


class Access:
    def __init__(self):
        self.ready = True
        self.checked = []

    def inspect(self, link):
        self.checked.append(link)
        if not self.ready:
            return {
                "status": "authorization_required",
                "full_name": "owner/repo",
                "authorization_url": "https://github.com/apps/synthetic-app/installations/new",
            }
        return {
            "status": "ready",
            "full_name": "owner/repo",
            "github_id": 10,
            "app_id": 20,
            "installation_id": 30,
            "main_branch": "main",
        }


class Binder:
    def __init__(self):
        self.writes = []
        self.enabled = True

    def supported(self):
        return self.enabled

    def bind(self, verified):
        self.writes.append(verified)
        return {
            "id": "00000000-0000-0000-0000-000000000001",
            "full_name": verified["full_name"],
            "status": "active",
            "created": True,
            "installation_id": verified["installation_id"],
        }


def service(path: Path):
    from github_intelligence.onboarding import RepositoryOnboarding

    access, binder = Access(), Binder()
    registry = RepositoryRegistry(path)
    onboarding = RepositoryOnboarding(access, binder, registry, client="personal", project="pilot")
    return onboarding, access, binder, registry


def confirmation(link="owner/repo"):
    return {
        "link": link,
        "expected": {
            "full_name": "owner/repo",
            "github_id": 10,
            "app_id": 20,
            "installation_id": 30,
        },
    }


__all__ = ["Access", "Binder", "confirmation", "service"]
