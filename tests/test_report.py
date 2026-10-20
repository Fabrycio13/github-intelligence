from datetime import datetime, timezone
from typing import Any

import pytest

from github_intelligence.models import (
    Evidence,
    PullRequestSnapshot,
    ReportWindow,
    RepositoryConfig,
    RepositorySnapshot,
)
from github_intelligence.report import render_report

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
WINDOW = ReportWindow(since=datetime(2026, 10, 18, tzinfo=timezone.utc), until=NOW)
CONFIG = RepositoryConfig(full_name="owner/repo")


def test_empty_report_is_honest_and_labels_auth_and_interval() -> None:
    from github_intelligence.report import render_report

    result = render_report([RepositorySnapshot(config=CONFIG, observed_at=NOW)], WINDOW)
    assert "[2026-10-18T00:00:00Z, 2026-10-19T00:00:00Z) UTC" in result
    assert "público" in result
    assert "Nenhum PR" in result
    assert "não comprova ausência de atividade" in result
    assert "Observado em" in result
    assert "deploy" in result
    assert "validação funcional" in result


def pr(**overrides: Any) -> PullRequestSnapshot:
    pull = {
        "number": 1,
        "title": "Change",
        "state": "open",
        "head": {"sha": "head"},
        "base": {"ref": "main"},
        "user": {"login": "author"},
        "html_url": "https://github.com/owner/repo/pull/1",
    }
    pull.update(overrides.pop("pull", {}))
    return PullRequestSnapshot(repository="owner/repo", pull=pull, observed_at=NOW, **overrides)


def report(item: PullRequestSnapshot, **overrides: Any) -> str:
    return render_report(
        [RepositorySnapshot(config=CONFIG, observed_at=NOW, pulls=[item], **overrides)], WINDOW
    )


def test_current_state_separate_from_events_with_people_not_rankings() -> None:
    result = report(
        pr(
            pull={
                "created_at": "2020-01-01T00:00:00Z",
                "state": "closed",
                "merged_at": "2020-01-02T00:00:00Z",
                "merged_by": {"login": "merger"},
            },
            reviews=[
                {"state": "APPROVED", "user": {"login": "reviewer"}, "submitted_at": "2020-01-02T00:00:00Z"}
            ],
        )
    )
    assert "Estados atuais" in result
    assert "Eventos no intervalo" in result
    assert "Nenhum evento tipado observado no intervalo" in result
    assert "autor: author" in result
    assert "revisores: reviewer" in result
    assert "merge por: merger" in result
    assert "observadas: 1" in result
    assert "confirmadas: desconhecidas" in result
    assert "2020-01-02" not in result
    assert "ranking" in result


@pytest.mark.parametrize(
    ("stamp", "included"),
    [
        ("2026-10-17T23:59:59Z", False),
        ("2026-10-18T00:00:00Z", True),
        ("2026-10-18T23:59:59Z", True),
        ("2026-10-19T00:00:00Z", False),
        ("2026-10-18T03:00:00+03:00", True),
        ("invalid", False),
    ],
)
def test_typed_events_have_half_open_utc_boundaries(stamp: str, included: bool) -> None:
    result = report(
        pr(
            pull={"created_at": stamp, "merged_at": stamp, "closed_at": stamp},
            reviews=[{"state": "COMMENTED", "submitted_at": stamp, "user": {"login": "r"}}],
            commits=[{"sha": "abc", "commit": {"committer": {"date": stamp}}}],
        )
    )
    events = result.split("### Eventos no intervalo")[1].split("### Estados atuais")[0]
    for label in ("PR criado", "PR integrado", "PR fechado", "Review submetido", "Commit registrado"):
        assert (label in events) == included


def test_updated_and_author_dates_do_not_prove_commit_events() -> None:
    result = report(
        pr(
            pull={"updated_at": "2026-10-18T12:00:00Z"},
            commits=[
                {"sha": "abc", "commit": {"author": {"date": "2026-10-18T12:00:00Z"}}},
            ],
        )
    )
    assert "Nenhum evento tipado observado no intervalo" in result
    assert (
        "Commit registrado" not in result.split("### Eventos no intervalo")[1].split("### Estados atuais")[0]
    )


def test_incomplete_repository_downgrades_assessment() -> None:
    result = report(
        pr(
            reviews=[
                {"state": "APPROVED", "user": {"login": "r1"}},
                {"state": "APPROVED", "user": {"login": "r2"}},
            ]
        ),
        complete=False,
        limitations=["reviews failed"],
        authenticated=True,
    )
    assert "INCOMPLETO" in result
    assert "reviews failed" in result
    assert "GitHub App" in result
    assert "revisão: unknown" in result
    assert "observadas: 2" in result
    assert "approved_observed" not in result


def test_security_escapes_untrusted_markdown_html_and_rejects_urls() -> None:
    result = report(
        pr(
            pull={
                "title": "<script>x</script> [evil](javascript:alert(1))\n## fake",
                "html_url": "javascript:alert(1)",
                "user": {"login": "[x](https://evil.test)"},
                "body": "SECRET BODY",
            },
            reviews=[
                {
                    "state": "COMMENTED",
                    "submitted_at": "2026-10-18T12:00:00Z",
                    "html_url": "https://github.com.evil.test/x",
                    "body": "SECRET REVIEW",
                }
            ],
            commits=[
                {
                    "sha": "a",
                    "html_url": "https://evil.test",
                    "patch": "SECRET DIFF",
                    "commit": {"committer": {"date": "2026-10-18T12:00:00Z"}},
                }
            ],
        )
    )
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "[evil](javascript:" not in result
    assert "\n## fake" not in result
    assert "](https://evil.test)" not in result
    assert "github.com.evil.test" not in result
    assert "SECRET" not in result
    assert "https://github.com/owner/repo/pull/1" in result


def test_evidence_urls_are_resanitized_for_markdown_delimiters() -> None:
    result = report(
        pr(evidence=[Evidence(kind="raw", url="https://github.com/o/r/a)evil(", observed_at=NOW)])
    )
    assert "/a)evil(" not in result
    assert "%29evil%28" in result


def test_questions_global_cap_and_deterministic_output() -> None:
    repos = [
        RepositorySnapshot(
            config=RepositoryConfig(full_name=f"owner/repo{i}"),
            observed_at=NOW,
            pulls=[pr(pull={"draft": True})],
            complete=False,
        )
        for i in range(6)
    ]
    result = render_report(repos, WINDOW)
    questions = result.split("## Perguntas do dia")[1].split("## Limitações")[0]
    assert 1 <= sum(line.startswith("- ") for line in questions.splitlines()) <= 3
    assert render_report(repos, WINDOW) == result
    assert "DISMISSED" in result
    assert "CODEOWNERS" in result
    assert "updated_at" in result


def test_quotes_and_literal_entities_cannot_break_text_escaping() -> None:
    result = report(pr(pull={"title": "O'Reilly &lt;script&gt; # heading"}))
    assert "O&#x27;Reilly" in result
    assert "&amp;lt;script&amp;gt;" in result
    assert "&#35; heading" in result


def test_no_repositories_is_explicit() -> None:
    assert "Nenhum repositório" in render_report([], WINDOW)
