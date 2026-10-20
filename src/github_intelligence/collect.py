"""Read-only current snapshots; report windows do not reconstruct past state."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .github import GitHubError
from .models import Evidence, PullRequestSnapshot, ReportWindow, RepositoryConfig, RepositorySnapshot

if TYPE_CHECKING:
    from .github import GitHubClient

COVERAGE_LIMITATIONS = [
    "Rules, CODEOWNERS, conversations, and permissions are not fully verified; "
    "full merge eligibility is never asserted.",
    "Deployments unavailable: not collected in this POC.",
    "Snapshot is current; the report window selects events, not historical state reconstruction.",
]


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant if instant.tzinfo is not None else None
    except ValueError:
        return None


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("Malformed GitHub collection")
    return value


def _summary(row: dict[str, Any]) -> None:
    if (
        type(row.get("number")) is not int
        or row["number"] < 1
        or row.get("state") not in ("open", "closed")
        or _timestamp(row.get("updated_at")) is None
    ):
        raise ValueError("Malformed PR list entry")


def _pull(value: Any, number: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Malformed PR detail")
    _summary(value)
    if (
        value["number"] != number
        or not isinstance(value.get("title"), str)
        or type(value.get("commits")) is not int
        or value["commits"] < 0
        or (value.get("mergeable") is not None and type(value["mergeable"]) is not bool)
    ):
        raise ValueError("Malformed PR detail")
    for field in ("head", "base"):
        branch = value.get(field)
        if (
            not isinstance(branch, dict)
            or not isinstance(branch.get("sha"), str)
            or re.fullmatch(r"[A-Za-z0-9]{1,128}", branch["sha"]) is None
            or not isinstance(branch.get("ref"), str)
            or not branch["ref"]
        ):
            raise ValueError("Malformed PR branch detail")
    _actor(value.get("user"))
    _actor(value.get("merged_by"))
    if value.get("draft") is not None and type(value["draft"]) is not bool:
        raise ValueError("Malformed PR draft state")
    return value


def _actor(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("Malformed GitHub actor")
    if value.get("login") is not None and not isinstance(value["login"], str):
        raise ValueError("Malformed GitHub actor login")


def _source_rows(value: Any, kind: str) -> list[dict[str, Any]]:
    rows = _rows(value)
    for row in rows:
        if kind == "reviews":
            _actor(row.get("user"))
        if kind == "checks" and row.get("app") is not None and not isinstance(row["app"], dict):
            raise ValueError("Malformed GitHub check app")
        if kind == "commits":
            if not isinstance(row.get("sha"), str) or not row["sha"]:
                raise ValueError("Malformed commits response")
            commit = row.get("commit", {})
            if not isinstance(commit, dict) or not isinstance(commit.get("committer", {}), dict):
                raise ValueError("Malformed commit metadata")
        else:
            if type(row.get("id")) is not int or row["id"] < 1:
                raise ValueError("Malformed source identifier")
            if kind in {"reviews", "statuses"} and not isinstance(row.get("state"), str):
                raise ValueError("Malformed source state")
            if kind == "reviews" and row.get("state") not in {
                "APPROVED",
                "CHANGES_REQUESTED",
                "COMMENTED",
                "DISMISSED",
                "PENDING",
            }:
                raise ValueError("Malformed review state")
            if kind == "statuses" and row.get("state") not in {"pending", "success", "failure", "error"}:
                raise ValueError("Malformed commit status")
            if (
                kind == "checks"
                and row.get("conclusion") is not None
                and not isinstance(row["conclusion"], str)
            ):
                raise ValueError("Malformed check conclusion")
    return rows


def _evidence(kind: str, path: str, rows: list[dict[str, Any]]) -> list[Evidence]:
    observed = datetime.now(timezone.utc)
    evidence = [Evidence(kind=kind, url="https://api.github.com" + path, observed_at=observed)]
    for row in rows:
        occurred = _timestamp(row.get("submitted_at") or row.get("created_at") or row.get("updated_at"))
        if kind == "checks":
            occurred = _timestamp(row.get("completed_at") or row.get("started_at"))
        if kind == "commits":
            occurred = _timestamp(row.get("commit", {}).get("committer", {}).get("date"))
        if occurred is not None:
            evidence.append(
                Evidence(
                    kind=kind,
                    url="https://api.github.com" + path,
                    observed_at=observed,
                    occurred_at=occurred,
                )
            )
    return evidence


def _collect_pull(client: GitHubClient, config: RepositoryConfig, number: int) -> PullRequestSnapshot:
    root = f"/repos/{config.full_name}"
    path = f"{root}/pulls/{number}"
    pull = _pull(client.get(path), number)
    evidence = _evidence("pull", path, [pull])
    sources: dict[str, list[dict[str, Any]]] = {}
    limitations: list[str] = []
    sha = pull["head"]["sha"]
    for kind, endpoint, params, key in (
        ("reviews", path + "/reviews", None, None),
        ("commits", path + "/commits", None, None),
        ("checks", f"{root}/commits/{sha}/check-runs", {"filter": "latest"}, "check_runs"),
        ("statuses", f"{root}/commits/{sha}/statuses", None, None),
    ):
        try:
            rows = _source_rows(client.paginate(endpoint, params=params, key=key), kind)
        except (GitHubError, ValueError) as exc:
            sources[kind] = []
            code = exc.status_code if isinstance(exc, GitHubError) else None
            status = f" (HTTP {code})" if type(code) is int else ""
            limitations.append(f"{kind} unavailable or malformed{status}.")
            continue
        if kind == "statuses":
            # GitHub status rows omit SHA; their request endpoint supplies this provenance.
            rows = [dict(row, sha=sha) for row in rows]
        sources[kind] = rows
        evidence.extend(_evidence(kind, endpoint, rows))
    if len(sources["commits"]) >= 250 or pull["commits"] > len(sources["commits"]):
        limitations.append(
            "commits may be truncated: PR commits endpoint has a 250 commit cap or count mismatch."
        )
    consistent = True
    try:
        final = _pull(client.get(path), number)
        evidence.extend(_evidence("pull_recheck", path, [final]))
        if any(
            pull.get(field) != final.get(field)
            for field in ("head", "base", "state", "draft", "merged", "merged_at", "closed_at")
        ):
            consistent = False
            limitations.append(
                "PR head, base, state, or draft changed during collection; snapshot is inconsistent."
            )
        if "draft" in final:
            pull = dict(pull, draft=final["draft"])
        if final["state"] == "open" and final.get("mergeable") is None:
            consistent = False
            limitations.append("Mergeability is unknown (null); it is not false.")
        elif consistent:
            # This bounded recheck can resolve GitHub's asynchronous mergeability calculation.
            pull = dict(pull, mergeable=final.get("mergeable"), mergeable_state=final.get("mergeable_state"))
    except (GitHubError, ValueError) as exc:
        consistent = False
        code = exc.status_code if isinstance(exc, GitHubError) else None
        status = f" (HTTP {code})" if type(code) is int else ""
        limitations.append(f"PR consistency recheck unavailable or malformed{status}.")
    return PullRequestSnapshot(
        repository=config.full_name,
        pull=pull,
        **sources,
        evidence=evidence,
        consistent=consistent,
        observed_at=datetime.now(timezone.utc),
        limitations=limitations,
        coverage_notes=list(COVERAGE_LIMITATIONS),
    )


def collect_repository(
    client: GitHubClient, config: RepositoryConfig, window: ReportWindow
) -> RepositorySnapshot:
    """Collect active repositories; core failures cannot masquerade as no PRs."""
    if config.status != "active":
        raise ValueError("Only active repositories may be collected")
    try:
        return _collect_repository(client, config, window)
    except GitHubError as exc:
        status = exc.status_code if type(exc.status_code) is int else None
        suffix = f" (HTTP {status})" if status is not None else ""
        raise GitHubError(f"Repository collection failed{suffix}", status_code=status) from None


def _collect_repository(
    client: GitHubClient, config: RepositoryConfig, window: ReportWindow
) -> RepositorySnapshot:
    root = f"/repos/{config.full_name}"
    metadata = client.get(root)
    if not isinstance(metadata, dict) or type(metadata.get("id")) is not int or metadata["id"] <= 0:
        raise ValueError("Malformed repository metadata")
    if config.github_id is not None and config.github_id != metadata["id"]:
        raise ValueError("Repository identity mismatch")
    rows = _rows(
        client.paginate(root + "/pulls", params={"state": "all", "sort": "updated", "direction": "desc"})
    )
    for row in rows:
        _summary(row)
    pulls = [
        _collect_pull(client, config, row["number"])
        for row in rows
        if row["state"] == "open"
        or (_timestamp(row["updated_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= window.since
    ]
    return RepositorySnapshot(
        config=config,
        pulls=pulls,
        complete=all(not pull.limitations and pull.consistent for pull in pulls),
        observed_at=datetime.now(timezone.utc),
        github_id=metadata["id"],
        authenticated=client.authenticated,
        coverage_notes=list(COVERAGE_LIMITATIONS),
    )
