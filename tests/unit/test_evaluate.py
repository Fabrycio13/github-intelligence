from datetime import datetime, timezone
from typing import Any

import pytest

from github_intelligence.evaluate import evaluate
from github_intelligence.models import PullRequestSnapshot, RepositoryConfig

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
CONFIG = RepositoryConfig(full_name="owner/repo", staging_branch="staging")


def snapshot(**overrides: Any) -> PullRequestSnapshot:
    pull = {
        "number": 1,
        "title": "Change",
        "html_url": "https://github.com/owner/repo/pull/1",
        "state": "open",
        "draft": False,
        "head": {"sha": "current"},
        "base": {"ref": "staging"},
        "user": {"login": "author"},
        "mergeable": True,
        "mergeable_state": "clean",
    }
    pull.update(overrides.pop("pull", {}))
    return PullRequestSnapshot(repository="owner/repo", pull=pull, observed_at=NOW, **overrides)


def review(login: str, state: str = "APPROVED", **extra: Any) -> dict[str, Any]:
    return {
        "user": {"login": login},
        "state": state,
        "commit_id": "current",
        "submitted_at": "2026-10-18T10:00:00Z",
        **extra,
    }


def test_missing_reviewer_identity_is_not_a_distinct_approval() -> None:
    result = evaluate(
        snapshot(reviews=[review("one"), {"user": {"login": None}, "state": "APPROVED"}]), CONFIG
    )
    assert result.approvals_observed == 1
    assert result.review == "unknown"


def test_zero_approvals_are_not_eligible() -> None:
    from github_intelligence.evaluate import evaluate

    result = evaluate(snapshot(), CONFIG)
    assert result.review == "no_approvals"
    assert result.approvals_observed == 0
    assert result.approvals_confirmed is None
    assert result.merge_eligibility == "unknown"
    assert result.ci == "not_observed"
    assert result.evidence
    assert result.verified_at == NOW


@pytest.mark.parametrize(
    ("reviews", "count", "state"),
    [
        ([review("one")], 1, "awaiting_approvals"),
        ([review("one"), review("two")], 2, "approved_observed"),
        ([review("one"), review("one", "COMMENTED")], 1, "awaiting_approvals"),
        ([review("one"), review("one", "PENDING")], 1, "awaiting_approvals"),
        ([review("one"), review("one", "DISMISSED")], 0, "no_approvals"),
        ([review("author")], 0, "no_approvals"),
        ([review("one"), review("two", "CHANGES_REQUESTED")], 1, "changes_requested"),
        ([review("One"), review("one")], 1, "awaiting_approvals"),
    ],
)
def test_current_distinct_decisional_reviews(reviews: list[dict[str, Any]], count: int, state: str) -> None:
    result = evaluate(snapshot(reviews=reviews), CONFIG)
    assert result.approvals_observed == count
    assert result.review == state
    assert result.approvals_confirmed is None
    assert result.merge_eligibility != "eligible"
    if state == "changes_requested":
        assert result.merge_eligibility == "unknown"


def test_old_sha_is_observed_with_caveat_not_automatically_invalidated() -> None:
    result = evaluate(snapshot(reviews=[review("one", commit_id="old")]), CONFIG)
    assert result.approvals_observed == 1
    assert "SHA" in " ".join(result.reasons)
    assert result.merge_eligibility == "unknown"


@pytest.mark.parametrize("overrides", [{"consistent": False}, {"limitations": ["reviews failed"]}])
def test_partial_review_state_unknown_but_counts_descriptive(overrides: dict[str, Any]) -> None:
    result = evaluate(snapshot(reviews=[review("one"), review("two")], **overrides), CONFIG)
    assert result.review == "unknown"
    assert result.approvals_observed == 2
    assert result.merge_eligibility == "unknown"


def test_review_order_is_timestamp_not_input_order() -> None:
    result = evaluate(
        snapshot(
            reviews=[
                review("one", "CHANGES_REQUESTED", submitted_at="2026-10-19T00:00:00Z"),
                review("one", submitted_at="2026-10-18T00:00:00Z"),
            ]
        ),
        CONFIG,
    )
    assert result.review == "changes_requested"


@pytest.mark.parametrize(
    ("pull", "lifecycle", "integration"),
    [
        ({"draft": True}, "draft", "unknown"),
        ({"state": "closed"}, "closed", "unknown"),
        ({"state": "closed", "merged_at": "2026-10-18T12:00:00Z"}, "merged", "unknown"),
        ({"merged": True, "base": {"ref": "main"}}, "merged", "unknown"),
        ({"merged": True, "base": {}}, "merged", "unknown"),
    ],
)
def test_independent_lifecycle_integration(pull: dict[str, Any], lifecycle: str, integration: str) -> None:
    result = evaluate(snapshot(pull=pull), CONFIG)
    assert result.lifecycle == lifecycle
    assert result.integration == integration
    assert result.deployment == result.validation == "unknown"
    assert result.merge_eligibility != "potentially_eligible"
    if lifecycle == "draft":
        assert result.merge_eligibility == "blocked"


def test_staging_unset_never_inferred() -> None:
    result = evaluate(snapshot(pull={"merged": True}), RepositoryConfig(full_name="owner/repo"))
    assert result.integration == "unknown"


@pytest.mark.parametrize(
    "pull", [{"mergeable": False}, {"mergeable_state": "blocked"}, {"mergeable_state": "dirty"}]
)
def test_conflict_and_blocked_are_reasons(pull: dict[str, Any]) -> None:
    result = evaluate(snapshot(pull=pull), CONFIG)
    assert result.merge_eligibility == "blocked"
    assert len(result.reasons) > 1


@pytest.mark.parametrize(
    ("checks", "statuses", "expected"),
    [
        ([], [{"context": "test", "state": "failure", "sha": "current"}], "failing"),
        ([], [{"context": "test", "state": "success", "sha": "old"}], "not_observed"),
        ([], [{"context": "test", "state": "pending", "sha": "current"}], "pending"),
        (
            [{"name": "test", "head_sha": "current", "status": "completed", "conclusion": "success"}],
            [],
            "passing",
        ),
        (
            [{"name": "test", "head_sha": "current", "status": "completed", "conclusion": "neutral"}],
            [],
            "unknown",
        ),
        (
            [{"name": "test", "head_sha": "current", "status": "completed", "conclusion": "skipped"}],
            [],
            "unknown",
        ),
        ([{"name": "test", "head_sha": "old", "conclusion": "failure"}], [], "not_observed"),
        ([{"name": "test", "conclusion": "success"}], [], "unknown"),
    ],
)
def test_current_head_ci(checks: list[dict[str, Any]], statuses: list[dict[str, Any]], expected: str) -> None:
    result = evaluate(snapshot(checks=checks, statuses=statuses), CONFIG)
    assert result.ci == expected
    if expected == "failing":
        # Failure is observable, but whether the check is mandatory is not known.
        assert result.merge_eligibility == "unknown"
        assert "obrigat" in " ".join(result.reasons)


def test_latest_status_per_context_and_check_per_app() -> None:
    statuses = [
        {"context": "test", "sha": "current", "state": "success", "created_at": "2026-10-19T00:00:00Z"},
        {"context": "test", "sha": "current", "state": "failure", "created_at": "2026-10-18T00:00:00Z"},
    ]
    checks = [
        {
            "name": "lint",
            "head_sha": "current",
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 1},
            "id": 2,
            "started_at": "2026-10-19T00:00:00Z",
        },
        {
            "name": "lint",
            "head_sha": "current",
            "status": "completed",
            "conclusion": "failure",
            "app": {"id": 1},
            "id": 1,
            "started_at": "2026-10-18T00:00:00Z",
        },
    ]
    assert evaluate(snapshot(checks=checks, statuses=statuses), CONFIG).ci == "passing"
    checks.append(
        {
            "name": "lint",
            "head_sha": "current",
            "status": "completed",
            "conclusion": "failure",
            "app": {"id": 2},
        }
    )
    assert evaluate(snapshot(checks=checks, statuses=statuses), CONFIG).ci == "failing"


def test_incomplete_ci_cannot_claim_success() -> None:
    result = evaluate(
        snapshot(
            consistent=False,
            statuses=[
                {"context": "test", "sha": "current", "state": "success"},
            ],
        ),
        CONFIG,
    )
    assert result.ci == "unknown"


def test_current_base_and_inconsistent_snapshot_do_not_prove_historical_integration() -> None:
    result = evaluate(
        snapshot(pull={"state": "closed", "merged_at": "2026-10-18T12:00:00Z"}, consistent=False), CONFIG
    )
    assert result.lifecycle == "merged"
    assert result.integration == "unknown"
    assert "base" in " ".join(result.reasons).casefold()
