"""Typed contracts. Unknown and unavailable must never silently become false."""

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Contract):
    kind: str = Field(min_length=1)
    url: str
    observed_at: AwareDatetime
    occurred_at: AwareDatetime | None = None

    @field_validator("url")
    @classmethod
    def github_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "api.github.com"}:
            raise ValueError("Evidence must use an HTTPS GitHub URL")
        if parsed.username or parsed.password or parsed.port or not parsed.path:
            raise ValueError("Invalid evidence URL")
        return value


class RepositoryConfig(Contract):
    full_name: str
    client_id: str = Field(default="personal", min_length=1)
    project_id: str = Field(default="pilot", min_length=1)
    status: str = "active"
    github_id: int | None = None
    github_installation_id: int | None = Field(default=None, strict=True, gt=0)
    staging_branch: str | None = None
    required_approvals: int = Field(default=2, ge=0)

    @field_validator("full_name")
    @classmethod
    def identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+", value):
            raise ValueError("Expected literal OWNER/REPO identifier")
        if value.split("/")[1] in {".", ".."}:
            raise ValueError("Invalid repository name")
        return value

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        if value not in {"active", "inactive", "removed"}:
            raise ValueError("Expected active, inactive or removed")
        return value


class PullRequestSnapshot(Contract):
    repository: str
    pull: dict[str, Any]
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    commits: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    statuses: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)
    consistent: bool = True
    observed_at: AwareDatetime


class RepositorySnapshot(Contract):
    config: RepositoryConfig
    observed_at: AwareDatetime
    pulls: list[PullRequestSnapshot] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)
    authenticated: bool = False
    complete: bool = True
    github_id: int | None = None


class Assessment(Contract):
    number: int
    title: str
    url: str
    lifecycle: str
    review: str
    ci: str
    merge_eligibility: str
    integration: str
    deployment: str = "unknown"
    validation: str = "unknown"
    approvals_observed: int = 0
    approvals_confirmed: int | None = None
    reasons: list[str] = Field(default_factory=list)
    evidence: list[Evidence]
    verified_at: AwareDatetime


class ReportWindow(Contract):
    since: AwareDatetime
    until: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "ReportWindow":
        if self.since >= self.until:
            raise ValueError("since must be earlier than until")
        return self

    def contains(self, value: str | datetime | None) -> bool:
        if value is None:
            return False
        try:
            instant = (
                datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
            )
            return instant.tzinfo is not None and self.since <= instant < self.until
        except (TypeError, ValueError):
            return False
