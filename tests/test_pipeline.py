"""End-to-end CLI exercise using explicitly synthetic HTTP fixtures, never live data."""

import json

import httpx
import pytest

import github_intelligence.__main__ as cli
from github_intelligence.github import GitHubClient

NOW = "2026-10-18T12:00:00Z"
SHA = "a" * 40


def test_complete_pipeline_uses_only_get_and_never_promotes_merge_to_deploy(tmp_path, monkeypatch):
    calls = []
    pull = {
        "id": 1001,
        "number": 1,
        "title": "SYNTHETIC: test change",
        "state": "closed",
        "html_url": "https://github.com/test-owner/test-repo/pull/1",
        "created_at": NOW,
        "updated_at": NOW,
        "closed_at": NOW,
        "merged_at": NOW,
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "commits": 1,
        "user": {"id": 100, "login": "test-author"},
        "merged_by": {"id": 101, "login": "test-merger"},
        "head": {"sha": SHA, "ref": "feature"},
        "base": {"sha": "b" * 40, "ref": "staging"},
    }

    def handler(request):
        calls.append(request.method)
        path = request.url.path
        if path == "/repos/test-owner/test-repo":
            data = {"id": 1234, "full_name": "test-owner/test-repo", "private": False}
        elif path.endswith("/pulls"):
            data = [pull]
        elif path.endswith("/pulls/1"):
            data = pull
        elif path.endswith("/reviews"):
            data = [
                {
                    "id": 200,
                    "state": "APPROVED",
                    "submitted_at": NOW,
                    "commit_id": SHA,
                    "user": {"id": 102, "login": "test-reviewer"},
                    "html_url": "https://github.com/test-owner/test-repo/pull/1#pullrequestreview-200",
                }
            ]
        elif path.endswith("/commits"):
            data = [
                {
                    "sha": SHA,
                    "html_url": f"https://github.com/test-owner/test-repo/commit/{SHA}",
                    "author": {"login": "test-author"},
                    "commit": {"message": "SYNTHETIC test", "committer": {"date": NOW}},
                }
            ]
        elif path.endswith("/check-runs"):
            data = {"total_count": 0, "check_runs": []}
        elif path.endswith("/statuses"):
            # Real GitHub status objects identify the SHA in the endpoint URL, not a sha field.
            data = [{"id": 300, "context": "synthetic-ci", "state": "success", "created_at": NOW}]
        else:
            raise AssertionError(f"Unexpected test request: {path}")
        return httpx.Response(200, json=data)

    monkeypatch.setattr(cli, "_client", lambda public: GitHubClient(transport=httpx.MockTransport(handler)))
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "full_name": "test-owner/test-repo",
                        "github_id": 1234,
                        "staging_branch": "staging",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "synthetic-report.md"
    code = cli.main(
        [
            "--public",
            "--config",
            str(config),
            "report",
            "--since",
            "2026-10-18T00:00:00Z",
            "--until",
            "2026-10-19T00:00:00Z",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    assert set(calls) == {"GET"}
    text = output.read_text(encoding="utf-8")
    assert "SYNTHETIC" in text
    assert "test-owner/test-repo" in text
    assert "test-reviewer" in text
    assert "revisão: awaiting_approvals" in text
    assert "CI: passing" in text
    assert "INCOMPLETO" not in text
    assert "deploy" in text.casefold()
    assert (
        "não verificável" in text.casefold()
        or "unknown" in text.casefold()
        or "desconhecido" in text.casefold()
    )


@pytest.mark.parametrize("bad_status,bad_body", [(404, {"message": "SYNTHETIC denied"}), (200, {"id": 0})])
def test_one_denied_repository_does_not_suppress_other_repository(
    tmp_path, monkeypatch, bad_status, bad_body
):
    def handler(request):
        if request.url.path == "/repos/test-owner/denied":
            return httpx.Response(bad_status, json=bad_body)
        if request.url.path == "/repos/test-owner/empty":
            return httpx.Response(200, json={"id": 321, "full_name": "test-owner/empty", "private": False})
        if request.url.path == "/repos/test-owner/empty/pulls":
            return httpx.Response(200, json=[])
        raise AssertionError("Unexpected test request")

    monkeypatch.setattr(cli, "_client", lambda public: GitHubClient(transport=httpx.MockTransport(handler)))
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repositories": [
                    {"full_name": "test-owner/denied"},
                    {"full_name": "test-owner/empty"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "synthetic-partial.md"
    assert (
        cli.main(
            [
                "--public",
                "--config",
                str(config),
                "report",
                "--since",
                "2026-10-18T00:00:00Z",
                "--until",
                "2026-10-19T00:00:00Z",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    text = output.read_text(encoding="utf-8")
    assert "test-owner/denied" in text
    assert "test-owner/empty" in text
    assert "parcial" in text.casefold()
