"""Collector contracts exercised with a public, deterministic transport fake."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from github_intelligence.collect import collect_repository
from github_intelligence.github import GitHubError
from github_intelligence.models import ReportWindow, RepositoryConfig

ROOT = "/repos/acme/demo"
WINDOW = ReportWindow(
    since=datetime(2026, 10, 1, tzinfo=timezone.utc), until=datetime(2026, 10, 15, tzinfo=timezone.utc)
)
CONFIG = RepositoryConfig(full_name="acme/demo")


class FakeClient:
    authenticated = True

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {ROOT: {"id": 123}, ROOT + "/pulls": []}
        self.calls: list[tuple[str, dict[str, Any] | None, str | None]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params, None))
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value()
        return deepcopy(value)

    def paginate(self, path: str, params: dict[str, Any] | None = None, key: str | None = None) -> Any:
        value = self.get(path, params)
        self.calls[-1] = (path, params, key)
        return value


def collect(client: FakeClient, config: RepositoryConfig = CONFIG) -> Any:
    return collect_repository(cast(Any, client), config, WINDOW)


def test_no_pulls_is_complete_with_explicit_coverage_limits() -> None:
    client = FakeClient()
    result = collect(client)
    assert result.complete
    assert result.pulls == []
    assert result.github_id == 123
    assert result.authenticated
    assert result.observed_at.tzinfo is not None
    assert result.observed_at <= datetime.now(timezone.utc)
    coverage = " ".join(result.coverage_notes).lower()
    for topic in ("rules", "codeowners", "conversations", "permissions", "eligibility", "deployment"):
        assert topic in coverage
    assert client.calls[1] == (
        ROOT + "/pulls",
        {"state": "all", "sort": "updated", "direction": "desc"},
        None,
    )


def with_pull(client: FakeClient, number: int = 1, **changes: Any) -> dict[str, Any]:
    pull = {
        "number": number,
        "state": "open",
        "title": "Example",
        "draft": False,
        "html_url": f"https://github.com/acme/demo/pull/{number}",
        "updated_at": "2026-09-01T00:00:00Z",
        "mergeable": True,
        "commits": 1,
        "head": {"sha": "abc", "ref": "feature"},
        "base": {"sha": "def", "ref": "main"},
    }
    pull.update(changes)
    client.responses[ROOT + "/pulls"].append(pull)
    client.responses[f"{ROOT}/pulls/{number}"] = pull
    client.responses[f"{ROOT}/pulls/{number}/reviews"] = [
        {"id": 10, "state": "APPROVED", "submitted_at": "2026-10-02T00:00:00Z"}
    ]
    client.responses[f"{ROOT}/pulls/{number}/commits"] = [
        {"sha": "abc", "commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}}
    ]
    client.responses[ROOT + "/commits/abc/check-runs"] = [{"id": 20, "conclusion": "success"}]
    client.responses[ROOT + "/commits/abc/statuses"] = [
        {"id": 30, "state": "success", "created_at": "2026-10-03T00:00:00Z"}
    ]
    return pull


@pytest.mark.parametrize(
    "source,payload",
    [
        ("reviews", {}),
        ("reviews", [{}]),
        ("reviews", [None]),
        ("reviews", [{"id": 10, "state": None}]),
        ("reviews", [{"id": 10, "state": []}]),
        ("reviews", [{"id": 10, "state": "APPROVED", "user": ["unexpected"]}]),
        ("reviews", [{"id": 10, "state": "APPROVED", "user": {"login": ["unexpected"]}}]),
        ("checks", [{"id": 20, "conclusion": "success", "app": ["unexpected"]}]),
        ("statuses", [{"id": 30, "state": {}}]),
        ("commits", [{"sha": "abc", "commit": "invalid"}]),
        ("commits", [{}]),
        ("checks", [{"id": "invalid", "conclusion": "success"}]),
        ("statuses", [{"id": 30, "state": None}]),
    ],
)
def test_malformed_optional_source_is_explicitly_partial(source: str, payload: Any) -> None:
    client = FakeClient()
    with_pull(client)
    endpoints = {
        "reviews": ROOT + "/pulls/1/reviews",
        "commits": ROOT + "/pulls/1/commits",
        "checks": ROOT + "/commits/abc/check-runs",
        "statuses": ROOT + "/commits/abc/statuses",
    }
    client.responses[endpoints[source]] = payload
    result = collect(client)
    assert not result.complete
    assert getattr(result.pulls[0], source) == []
    assert any(source in item and "malformed" in item for item in result.pulls[0].limitations)


@pytest.mark.parametrize(
    "rows",
    [
        None,
        {},
        [None],
        [{}],
        [{"number": True, "state": "open"}],
        [{"number": 1, "state": "closed", "updated_at": "garbage"}],
        [{"number": 1, "state": "unknown", "updated_at": "2026-10-01T00:00:00Z"}],
    ],
)
def test_malformed_pull_list_never_becomes_empty_success(rows: Any) -> None:
    client = FakeClient()
    client.responses[ROOT + "/pulls"] = rows
    with pytest.raises((GitHubError, ValueError)):
        collect(client)


@pytest.mark.parametrize(
    "field,value",
    [
        ("number", 2),
        ("state", "unknown"),
        ("updated_at", "bad"),
        ("head", None),
        ("head", {"sha": "../secret", "ref": "feature"}),
        ("base", {}),
        ("commits", "1"),
        ("mergeable", "false"),
        ("title", None),
    ],
)
def test_malformed_pull_detail_is_rejected(field: str, value: Any) -> None:
    client = FakeClient()
    pull = deepcopy(with_pull(client))
    pull[field] = value
    client.responses[ROOT + "/pulls/1"] = pull
    with pytest.raises((GitHubError, ValueError)):
        collect(client)


@pytest.mark.parametrize("status", ["inactive", "removed"])
def test_inactive_repository_never_contacts_github(status: str) -> None:
    client = FakeClient()
    with pytest.raises(ValueError, match="active"):
        collect(client, CONFIG.model_copy(update={"status": status}))
    assert client.calls == []


@pytest.mark.parametrize("endpoint", [ROOT, ROOT + "/pulls", ROOT + "/pulls/1"])
def test_core_api_denial_is_safe_and_never_empty_success(endpoint: str) -> None:
    client = FakeClient()
    with_pull(client)
    client.responses[endpoint] = GitHubError("SECRET_SENTINEL", 403)
    with pytest.raises((GitHubError, ValueError)) as captured:
        collect(client)
    assert "SECRET_SENTINEL" not in str(captured.value)
    assert "403" in str(captured.value)
    assert captured.value.__suppress_context__


@pytest.mark.parametrize("metadata", [{}, [], {"id": True}, {"id": "123"}, {"id": -1}])
def test_malformed_repository_metadata_is_rejected(metadata: Any) -> None:
    client = FakeClient()
    client.responses[ROOT] = metadata
    with pytest.raises((GitHubError, ValueError), match="metadata"):
        collect(client)


def test_repository_identity_mismatch_is_rejected_before_pulls() -> None:
    client = FakeClient()
    with pytest.raises((GitHubError, ValueError), match="identity"):
        collect(client, CONFIG.model_copy(update={"github_id": 999}))
    assert len(client.calls) == 1


@pytest.mark.parametrize("count,collected", [(251, 250), (250, 250), (5, 2)])
def test_commit_cap_or_short_collection_is_partial(count: int, collected: int) -> None:
    client = FakeClient()
    with_pull(client, commits=count)
    client.responses[ROOT + "/pulls/1/commits"] = [{"sha": f"sha{i}"} for i in range(collected)]
    result = collect(client)
    assert not result.complete
    assert len(result.pulls[0].commits) == collected
    assert "commits" in " ".join(result.pulls[0].limitations)
    assert any("250" in item or "truncated" in item for item in result.pulls[0].limitations)


@pytest.mark.parametrize("change", ["head", "base", "state", "mergeable", "recheck_error"])
def test_recheck_uncertainty_is_inconsistent_and_incomplete(change: str) -> None:
    client = FakeClient()
    original = with_pull(client)
    final = deepcopy(original)
    if change in {"head", "base"}:
        final[change]["sha"] = "changed"
    elif change == "state":
        final["state"] = "closed"
    elif change == "mergeable":
        final["mergeable"] = None
    calls = 0

    def detail() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original
        if change == "recheck_error":
            raise GitHubError("SECRET_SENTINEL", 404)
        return final

    client.responses[ROOT + "/pulls/1"] = detail
    result = collect(client)
    assert not result.complete
    assert not result.pulls[0].consistent
    assert result.pulls[0].pull["head"]["sha"] == "abc"
    assert "SECRET_SENTINEL" not in result.model_dump_json()
    assert result.pulls[0].limitations
    assert len(result.pulls[0].coverage_notes) == 3
    assert calls <= 3


def test_initial_null_mergeability_remains_unknown_even_if_recheck_resolves() -> None:
    client = FakeClient()
    with_pull(client, mergeable=None)
    result = collect(client)
    assert not result.complete
    assert not result.pulls[0].consistent
    assert result.pulls[0].pull["mergeable"] is None


@pytest.mark.parametrize("source", ["reviews", "commits", "checks", "statuses"])
def test_optional_source_failure_is_partial_without_leaking_error(source: str) -> None:
    client = FakeClient()
    with_pull(client)
    endpoints = {
        "reviews": ROOT + "/pulls/1/reviews",
        "commits": ROOT + "/pulls/1/commits",
        "checks": ROOT + "/commits/abc/check-runs",
        "statuses": ROOT + "/commits/abc/statuses",
    }
    client.responses[endpoints[source]] = GitHubError("SECRET_SENTINEL", status_code=403)
    result = collect(client)
    assert not result.complete
    assert len(result.pulls) == 1
    snapshot = result.pulls[0]
    assert getattr(snapshot, source) == []
    assert source in " ".join(snapshot.limitations)
    assert "403" in " ".join(snapshot.limitations)
    assert "SECRET_SENTINEL" not in result.model_dump_json()
    assert not any(item.kind == source for item in snapshot.evidence)
    assert {"reviews", "commits", "checks", "statuses"} - {source} <= {
        item.kind for item in snapshot.evidence
    }


def test_closed_selection_is_updated_since_not_until_state_reconstruction() -> None:
    client = FakeClient()
    with_pull(client, 1, state="closed", updated_at="2026-09-30T23:59:59Z")
    with_pull(client, 2, state="closed", updated_at="2026-10-01T00:00:00Z")
    with_pull(client, 3, state="closed", updated_at="2026-10-18T00:00:00Z")
    with_pull(client, 4, state="open", updated_at="2026-01-01T00:00:00Z")
    result = collect(client)
    assert [snapshot.pull["number"] for snapshot in result.pulls] == [2, 3, 4]
    assert not any(path == ROOT + "/pulls/1" for path, _, _ in client.calls)


def test_check_completion_time_is_preserved_as_event_evidence() -> None:
    client = FakeClient()
    with_pull(client)
    client.responses[ROOT + "/commits/abc/check-runs"][0]["completed_at"] = "2026-10-03T12:00:00Z"
    result = collect(client)
    assert any(
        item.kind == "checks" and item.occurred_at == datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
        for item in result.pulls[0].evidence
    )


def test_false_mergeability_is_known_not_inconsistent() -> None:
    client = FakeClient()
    with_pull(client, mergeable=False)
    result = collect(client)
    assert result.complete
    assert result.pulls[0].consistent
    assert result.pulls[0].pull["mergeable"] is False


def test_http_transport_paginates_every_source_without_code_access() -> None:
    import httpx

    from github_intelligence.github import GitHubClient

    fixture = FakeClient()
    with_pull(fixture, commits=2)
    paginated = {
        ROOT + "/pulls/1/reviews": [{"id": 11, "state": "COMMENTED"}],
        ROOT + "/pulls/1/commits": [{"sha": "second"}],
        ROOT + "/commits/abc/check-runs": [{"id": 21, "conclusion": None}],
        ROOT + "/commits/abc/statuses": [{"id": 31, "state": "pending"}],
    }
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        headers: dict[str, str] = {}
        if request.url.params.get("page") == "2":
            body = paginated[path]
        else:
            body = fixture.responses[path]
            if path in paginated:
                next_url = request.url.copy_merge_params({"page": "2"})
                headers["Link"] = f'<{next_url}>; rel="next"'
        if path.endswith("/check-runs"):
            assert request.url.params["filter"] == "latest"
            body = {"total_count": 2, "check_runs": body}
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert request.method == "GET"
        return httpx.Response(200, json=body, headers=headers)

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        result = collect_repository(client, CONFIG, WINDOW)
    assert result.complete
    assert not result.authenticated
    snapshot = result.pulls[0]
    for source in ("reviews", "commits", "checks", "statuses"):
        assert len(getattr(snapshot, source)) == 2
    assert not any("/contents" in request.url.path or "diff" in request.url.path for request in seen)


def test_open_pull_outside_window_keeps_raw_sources_and_evidence() -> None:
    client = FakeClient()
    with_pull(client)
    result = collect(client)
    assert result.complete
    assert len(result.pulls) == 1
    snapshot = result.pulls[0]
    assert snapshot.consistent
    assert snapshot.pull["head"]["sha"] == "abc"
    assert snapshot.reviews[0]["id"] == 10
    assert snapshot.commits[0]["sha"] == "abc"
    assert snapshot.checks[0]["conclusion"] == "success"
    assert snapshot.statuses[0]["state"] == "success"
    kinds = {item.kind for item in snapshot.evidence}
    assert {"pull", "reviews", "commits", "checks", "statuses", "pull_recheck"} <= kinds
    assert any(item.occurred_at is not None for item in snapshot.evidence)
    assert all(item.observed_at.tzinfo is not None for item in snapshot.evidence)
    assert (ROOT + "/commits/abc/check-runs", {"filter": "latest"}, "check_runs") in client.calls
    assert sum(path == ROOT + "/pulls/1" for path, _, _ in client.calls) == 2
    assert not any(path.endswith("/status") or "/contents" in path for path, _, _ in client.calls)


def test_merged_pull_null_mergeability_is_not_a_collection_failure() -> None:
    client = FakeClient()
    with_pull(
        client,
        state="closed",
        merged_at="2026-10-02T12:00:00Z",
        mergeable=None,
        updated_at="2026-10-02T12:00:00Z",
    )
    result = collect(client)
    assert result.complete
    assert result.pulls[0].consistent


def test_initial_null_mergeability_can_resolve_on_bounded_recheck() -> None:
    client = FakeClient()
    original = with_pull(client, mergeable=None)
    responses = iter([deepcopy(original), {**original, "mergeable": True}])
    client.responses[ROOT + "/pulls/1"] = lambda: next(responses)
    result = collect(client)
    assert result.complete
    assert result.pulls[0].consistent
    assert result.pulls[0].pull["mergeable"] is True


def test_draft_transition_is_detected_and_latest_flag_is_preserved() -> None:
    client = FakeClient()
    original = with_pull(client, draft=False)
    responses = iter([deepcopy(original), {**original, "draft": True}])
    client.responses[ROOT + "/pulls/1"] = lambda: next(responses)
    result = collect(client)
    assert not result.complete
    assert not result.pulls[0].consistent
    assert result.pulls[0].pull["draft"] is True
