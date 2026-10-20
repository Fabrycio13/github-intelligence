"""Persistence is a metadata allowlist, not general-purpose DLP."""

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from github_intelligence.evaluate import evaluate
from github_intelligence.models import (
    Evidence,
    PullRequestSnapshot,
    ReportWindow,
    RepositoryConfig,
    RepositorySnapshot,
)
from github_intelligence.persistence_payload import restore_snapshot, snapshot_payload
from github_intelligence.report import render_report

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
STAMP = "2026-10-18T12:00:00Z"
URL = "https://github.com/owner/repo/pull/1"


def sample() -> PullRequestSnapshot:
    actor = {"id": 7, "login": "author", "email": "SECRET_EMAIL", "token": "SECRET_TOKEN"}
    return PullRequestSnapshot(
        repository="owner/repo",
        observed_at=NOW,
        pull={
            "number": 1,
            "title": "Change",
            "state": "open",
            "draft": False,
            "created_at": STAMP,
            "updated_at": STAMP,
            "closed_at": None,
            "merged_at": None,
            "merged": False,
            "mergeable": None,
            "mergeable_state": "unknown",
            "commits": 1,
            "user": actor,
            "merged_by": None,
            "head": {"sha": "a" * 40, "ref": "feature", "repo": {"token": "SECRET_FORK"}},
            "base": {"sha": "b" * 40, "ref": "main", "repo": {"private_key": "SECRET_KEY"}},
            "html_url": URL,
            "body": "SECRET_BODY",
            "diff_url": "SECRET_DIFF",
        },
        reviews=[
            {
                "id": 10,
                "state": "APPROVED",
                "submitted_at": STAMP,
                "commit_id": "a" * 40,
                "user": {**actor, "login": "reviewer"},
                "html_url": URL + "#pullrequestreview-10",
                "body": "SECRET_REVIEW",
                "_links": {"token": "SECRET_LINK"},
            }
        ],
        commits=[
            {
                "sha": "a" * 40,
                "html_url": "https://github.com/owner/repo/commit/" + "a" * 40,
                "author": actor,
                "committer": actor,
                "commit": {
                    "author": {"name": "Author", "date": STAMP, "email": "SECRET_EMAIL"},
                    "committer": {"name": "Committer", "date": STAMP, "email": "SECRET_EMAIL"},
                    "message": "SECRET_MESSAGE",
                    "verification": {"signature": "SECRET_SIGNATURE"},
                },
                "files": [{"patch": "SECRET_PATCH"}],
            }
        ],
        checks=[
            {
                "id": 11,
                "name": "test",
                "head_sha": "a" * 40,
                "status": "completed",
                "conclusion": "success",
                "created_at": STAMP,
                "started_at": STAMP,
                "completed_at": STAMP,
                "app": {"id": 3, "slug": "ci", "name": "CI", "pem": "SECRET_PEM"},
                "html_url": URL,
                "details_url": "https://api.github.com/repos/owner/repo/check-runs/11",
                "output": {"text": "SECRET_OUTPUT"},
                "details": {"token": "SECRET_DETAILS"},
            }
        ],
        statuses=[
            {
                "id": 12,
                "context": "test",
                "state": "success",
                "sha": "a" * 40,
                "created_at": STAMP,
                "updated_at": STAMP,
                "target_url": URL,
                "url": "https://api.github.com/repos/owner/repo/statuses/" + "a" * 40,
                "description": "SECRET_DESCRIPTION",
            }
        ],
        evidence=[Evidence(kind="pull", url=URL, observed_at=NOW, occurred_at=None)],
        limitations=["Collector could not prove branch policy"],
        coverage_notes=["PR sample only"],
    )


def test_whitelist_removes_nested_secrets_without_mutating_original() -> None:
    original = sample()
    before = original.model_dump()
    payload = snapshot_payload(original)
    assert set(payload) == {"schema_version", "snapshot"}
    assert payload["schema_version"] == 1
    assert set(payload["snapshot"]) == {
        "repository",
        "pull",
        "reviews",
        "commits",
        "checks",
        "statuses",
        "evidence",
        "limitations",
        "coverage_notes",
        "consistent",
        "observed_at",
    }
    encoded = json.dumps(payload, allow_nan=False)
    assert "SECRET" not in encoded
    data = payload["snapshot"]
    assert data["pull"]["user"] == {"id": 7, "login": "author"}
    assert data["pull"]["head"] == {"sha": "a" * 40, "ref": "feature"}
    assert data["commits"][0]["commit"] == {
        "author": {"name": "Author", "date": STAMP},
        "committer": {"name": "Committer", "date": STAMP},
    }
    assert data["checks"][0]["app"] == {"id": 3, "slug": "ci", "name": "CI"}
    assert data["pull"]["mergeable"] is None
    assert data["pull"]["closed_at"] is None
    assert data["observed_at"] == NOW.isoformat()
    assert data["evidence"][0]["observed_at"] == NOW.isoformat()
    assert data["evidence"][0]["occurred_at"] is None
    assert original.model_dump() == before
    data["pull"]["user"]["login"] = "changed"
    assert original.pull["user"]["login"] == "author"


def test_restore_roundtrip_preserves_evaluator_and_report_semantics() -> None:
    original = sample()
    original.limitations = []
    # URL fragments are not needed for evidence; use canonical links in this comparison.
    original.reviews[0]["html_url"] = URL
    original.evidence[0].occurred_at = datetime.fromisoformat(STAMP)
    payload = json.loads(json.dumps(snapshot_payload(original)))
    restored = restore_snapshot(payload)
    assert snapshot_payload(restored) == payload
    assert restored.evidence[0].occurred_at == original.evidence[0].occurred_at
    assert restored.observed_at == NOW
    config = RepositoryConfig(full_name="owner/repo", required_approvals=1)
    assert evaluate(original, config) == evaluate(restored, config)
    window = ReportWindow(since=datetime(2026, 10, 18, tzinfo=timezone.utc), until=NOW)

    def report(item: PullRequestSnapshot) -> str:
        return render_report([RepositorySnapshot(config=config, observed_at=NOW, pulls=[item])], window)

    assert report(original) == report(restored)
    assert restored.pull["mergeable"] is None
    assert evaluate(restored, config).approvals_confirmed is None
    assert evaluate(restored, config).integration == "unknown"


@pytest.mark.parametrize("version", [None, True, False, 0, 2, "1", 1.0])
def test_restore_requires_exact_integer_schema_version(version: Any) -> None:
    payload = snapshot_payload(sample())
    payload["schema_version"] = version
    with pytest.raises(ValueError):
        restore_snapshot(payload)


@pytest.mark.parametrize(
    "path,value",
    [
        (("extra",), "secret"),
        (("snapshot", "extra"), "secret"),
        (("snapshot", "pull", "body"), "secret"),
        (("snapshot", "pull", "user", "email"), "secret"),
        (("snapshot", "pull", "user"), []),
        (("snapshot", "pull", "head"), []),
        (("snapshot", "pull", "number"), "1"),
        (("snapshot", "pull", "number"), True),
        (("snapshot", "pull", "number"), 0),
        (("snapshot", "pull", "draft"), "false"),
        (("snapshot", "pull", "merged"), 1),
        (("snapshot", "pull", "title"), {"secret": "text"}),
        (("snapshot", "pull", "created_at"), "not a date"),
        (("snapshot", "pull", "created_at"), 123),
        (("snapshot", "pull", "created_at"), "2026-10-18T12:00:00"),
        (("snapshot", "pull", "user", "id"), "7"),
        (("snapshot", "pull", "user", "login"), ["reviewer"]),
        (("snapshot", "consistent"), "false"),
        (("snapshot", "consistent"), None),
        (("snapshot", "observed_at"), NOW),
        (("snapshot", "observed_at"), "2026-10-19"),
        (("snapshot", "repository"), []),
        (("snapshot", "limitations"), [1]),
        (("snapshot", "coverage_notes"), ("note",)),
        (("snapshot", "reviews"), {}),
    ],
)
def test_restore_rejects_unknown_fields_and_invalid_json_types(path: tuple[str, ...], value: Any) -> None:
    payload = snapshot_payload(sample())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        restore_snapshot(payload)


@pytest.mark.parametrize("key", ["schema_version", "snapshot"])
def test_restore_rejects_missing_envelope_keys(key: str) -> None:
    payload = snapshot_payload(sample())
    del payload[key]
    with pytest.raises(ValueError):
        restore_snapshot(payload)


def test_restore_rejects_missing_snapshot_fields() -> None:
    payload = snapshot_payload(sample())
    del payload["snapshot"]["consistent"]
    with pytest.raises(ValueError):
        restore_snapshot(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user", []),
        ("merged_by", "person"),
        ("head", []),
        ("draft", "false"),
        ("merged", 1),
        ("number", "1"),
        ("commits", -1),
        ("updated_at", float("nan")),
    ],
)
def test_export_rejects_malformed_approved_fields(field: str, value: Any) -> None:
    original = sample()
    original.pull[field] = value
    with pytest.raises(ValueError):
        snapshot_payload(original)


@pytest.mark.parametrize(
    "collection,field,value",
    [
        ("reviews", "user", []),
        ("reviews", "state", ["APPROVED"]),
        ("reviews", "id", "10"),
        ("checks", "app", []),
        ("commits", "commit", []),
        ("statuses", "state", {"success": True}),
    ],
)
def test_invalid_nested_collection_fields_are_not_coerced(collection: str, field: str, value: Any) -> None:
    original = sample()
    getattr(original, collection)[0][field] = value
    with pytest.raises(ValueError):
        snapshot_payload(original)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",
        "https://evil.test/secret",
        "//github.com/owner/repo",
        "https://github.com.evil.test/secret",
        "https://token@github.com/owner/repo",
        "https://github.com:443/owner/repo",
        "https://github.com:/owner/repo",
        "https://github.com/owner/\\\\repo",
        "https://github.com/owner/repo\n",
        "https://github.com/owner/%0arepo",
        "https://github.com/owner/%5crepo",
        "https://github.com/owner/bad%ZZ",
        "https://github.com",
    ],
)
def test_untrusted_urls_drop_but_invalid_evidence_fails(url: str) -> None:
    original = sample()
    original.pull["html_url"] = url
    original.reviews[0]["html_url"] = url
    original.commits[0]["html_url"] = url
    original.checks[0]["html_url"] = url
    original.checks[0]["details_url"] = url
    original.statuses[0]["target_url"] = url
    original.statuses[0]["url"] = url
    payload = snapshot_payload(original)
    assert payload["snapshot"]["pull"].get("html_url") is None
    for collection in ("reviews", "commits", "checks", "statuses"):
        for key in ("html_url", "details_url", "target_url", "url"):
            assert payload["snapshot"][collection][0].get(key) is None
    # Model copies can bypass Pydantic: the persistence boundary must validate again.
    original.evidence[0] = original.evidence[0].model_copy(update={"url": url})
    with pytest.raises(ValueError):
        snapshot_payload(original)
    payload["snapshot"]["evidence"][0]["url"] = url
    with pytest.raises(ValueError):
        restore_snapshot(payload)


def test_urls_strip_queries_and_fragments_without_double_encoding_percent_paths() -> None:
    original = sample()
    url = "https://github.com/owner/repo/pull/1%2Ftest?token=SECRET_QUERY#SECRET_FRAGMENT"
    clean = "https://github.com/owner/repo/pull/1%2Ftest"
    original.pull["html_url"] = url
    original.evidence[0].url = url
    payload = snapshot_payload(original)
    assert payload["snapshot"]["pull"]["html_url"] == clean
    assert payload["snapshot"]["evidence"][0]["url"] == clean
    assert "SECRET" not in json.dumps(payload)
    payload["snapshot"]["evidence"][0]["url"] = url
    assert restore_snapshot(payload).evidence[0].url == clean


@pytest.mark.parametrize("collection", ["reviews", "commits", "checks", "statuses", "evidence"])
def test_collection_rows_must_be_objects(collection: str) -> None:
    payload = snapshot_payload(sample())
    payload["snapshot"][collection] = [None]
    with pytest.raises(ValueError):
        restore_snapshot(payload)
    if collection != "evidence":
        original = sample()
        setattr(original, collection, [None])
        with pytest.raises(ValueError):
            snapshot_payload(original)


@pytest.mark.parametrize("field,value", [("kind", ""), ("observed_at", None), ("occurred_at", 1)])
def test_invalid_evidence_is_rejected_not_replaced(field: str, value: Any) -> None:
    original = sample()
    original.evidence[0] = original.evidence[0].model_copy(update={field: value})
    with pytest.raises(ValueError):
        snapshot_payload(original)


@pytest.mark.parametrize("timestamp", ["123", "1700000000", "+1700000000", "1.5"])
def test_numeric_timestamp_strings_are_not_iso_dates(timestamp: str) -> None:
    original = sample()
    original.reviews[0]["submitted_at"] = timestamp
    with pytest.raises(ValueError):
        snapshot_payload(original)
    payload = snapshot_payload(sample())
    payload["snapshot"]["observed_at"] = timestamp
    with pytest.raises(ValueError):
        restore_snapshot(payload)


@pytest.mark.parametrize(
    "field,value",
    [("limitations", ("note",)), ("coverage_notes", ("note",)), ("consistent", "false")],
)
def test_export_does_not_coerce_invalid_outer_model_copies(field: str, value: Any) -> None:
    original = sample().model_copy(update={field: value})
    with pytest.raises(ValueError):
        snapshot_payload(original)


def test_optional_null_values_and_missing_fields_remain_unknown() -> None:
    original = PullRequestSnapshot(
        repository="owner/repo",
        observed_at=NOW,
        pull={"number": 1, "mergeable": None, "merged": None, "user": None, "head": None},
        reviews=[{"user": {"login": None}, "submitted_at": None, "state": "APPROVED"}],
    )
    restored = restore_snapshot(snapshot_payload(original))
    assert restored.pull == original.pull
    assert restored.reviews == original.reviews
    result = evaluate(restored, RepositoryConfig(full_name="owner/repo"))
    assert result.review == "unknown"
    assert result.approvals_observed == 0
    assert result.approvals_confirmed is None


def test_evidence_timezone_and_microseconds_survive_json_roundtrip() -> None:
    original = sample()
    instant = datetime.fromisoformat("2026-10-19T10:20:30.123456-03:00")
    original.observed_at = instant
    original.evidence[0].observed_at = instant
    original.evidence[0].occurred_at = instant
    payload = json.loads(json.dumps(snapshot_payload(original)))
    assert payload["snapshot"]["evidence"][0]["occurred_at"] == instant.isoformat()
    restored = restore_snapshot(payload)
    assert restored.observed_at == instant
    assert restored.evidence[0].observed_at == instant
    assert restored.evidence[0].occurred_at == instant
    assert restored.evidence[0].kind == "pull"


def test_restore_drops_offdomain_non_evidence_url_without_faking_evidence() -> None:
    payload = snapshot_payload(sample())
    payload["snapshot"]["pull"]["html_url"] = "https://evil.test/token"
    restored = restore_snapshot(payload)
    assert restored.pull.get("html_url") is None
    assert restored.evidence == sample().evidence
