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
    repo = overrides.pop("repository", "owner/repo")
    pull = {
        "number": 1,
        "title": "Change",
        "state": "open",
        "head": {"sha": "head"},
        "base": {"ref": "main"},
        "user": {"login": "author"},
        "html_url": f"https://github.com/{repo}/pull/1",
    }
    pull.update(overrides.pop("pull", {}))
    return PullRequestSnapshot(repository=repo, pull=pull, observed_at=NOW, **overrides)


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



def test_executive_summary_empty_snapshots() -> None:
    from github_intelligence.report import build_executive_summary

    summary = build_executive_summary([], WINDOW)
    assert summary["available"] is True
    assert summary["summary"]["total_prs_observed"] == 0
    assert summary["summary"]["merged_in_window_count"] == 0
    assert summary["summary"]["open_awaiting_review_count"] == 0
    assert summary["summary"]["open_drafts_count"] == 0
    assert summary["summary"]["potential_blockers_count"] == 0
    assert summary["merged_in_window"] == []
    assert summary["open_awaiting_review"] == []
    assert summary["open_drafts"] == []
    assert summary["potential_blockers"] == []
    assert summary["participants"] == []
    assert len(summary["limitations"]) == 4
    assert "# Relatório Executivo Semanal" in summary["markdown_text"]


def test_executive_summary_half_open_utc_window_boundaries() -> None:
    from github_intelligence.report import build_executive_summary

    # WINDOW is [2026-10-18T00:00:00Z, 2026-10-19T00:00:00Z)
    pr_before = pr(
        pull={"number": 1, "merged": True, "merged_at": "2026-10-17T23:59:59Z", "state": "closed"}
    )
    pr_exact_start = pr(
        pull={"number": 2, "merged": True, "merged_at": "2026-10-18T00:00:00Z", "state": "closed"}
    )
    pr_inside = pr(
        pull={"number": 3, "merged": True, "merged_at": "2026-10-18T12:00:00Z", "state": "closed"}
    )
    pr_exact_end = pr(
        pull={"number": 4, "merged": True, "merged_at": "2026-10-19T00:00:00Z", "state": "closed"}
    )
    pr_after = pr(
        pull={"number": 5, "merged": True, "merged_at": "2026-10-19T00:00:01Z", "state": "closed"}
    )

    summary = build_executive_summary(
        [pr_before, pr_exact_start, pr_inside, pr_exact_end, pr_after],
        WINDOW,
    )
    merged_numbers = [item["number"] for item in summary["merged_in_window"]]
    # Exact start (since) is included; exact end (until) is excluded!
    assert merged_numbers == [3, 2]
    assert summary["summary"]["merged_in_window_count"] == 2


def test_executive_summary_open_awaiting_review_and_blockers() -> None:
    from github_intelligence.report import build_executive_summary

    pr_awaiting = pr(
        pull={"number": 10, "state": "open", "title": "Awaiting", "created_at": "2026-10-18T10:00:00Z"},
    )
    pr_blocked_ci = pr(
        pull={"number": 11, "state": "open", "title": "Failing CI", "created_at": "2026-10-18T10:00:00Z"},
        checks=[
            {"name": "test", "head_sha": "head", "status": "completed", "conclusion": "failure", "id": 1}
        ],
    )
    pr_conflict = pr(
        pull={
            "number": 12,
            "state": "open",
            "title": "Conflicting",
            "mergeable": False,
            "mergeable_state": "dirty",
        },
    )
    pr_approved = pr(
        pull={"number": 13, "state": "open", "title": "Approved"},
        reviews=[
            {"state": "APPROVED", "user": {"login": "rev1"}, "submitted_at": "2026-10-18T11:00:00Z", "id": 1},
            {"state": "APPROVED", "user": {"login": "rev2"}, "submitted_at": "2026-10-18T11:30:00Z", "id": 2},
        ],
    )

    summary = build_executive_summary(
        [pr_awaiting, pr_blocked_ci, pr_conflict, pr_approved],
        WINDOW,
    )
    awaiting_nums = [item["number"] for item in summary["open_awaiting_review"]]
    blocker_nums = [item["number"] for item in summary["potential_blockers"]]

    assert 10 in awaiting_nums
    assert 11 in awaiting_nums
    assert 12 in awaiting_nums
    assert 13 not in awaiting_nums

    assert 11 in blocker_nums
    assert 12 in blocker_nums
    assert 10 not in blocker_nums
    assert 13 not in blocker_nums


def test_executive_summary_participants_without_ranking() -> None:
    from github_intelligence.report import build_executive_summary

    pr_item = pr(
        pull={
            "number": 20,
            "user": {"login": "Alice"},
            "merged": True,
            "merged_at": "2026-10-18T10:00:00Z",
            "merged_by": {"login": "Bob"},
        },
        reviews=[
            {"state": "APPROVED", "user": {"login": "charlie"}, "id": 1},
        ],
        commits=[
            {"sha": "c1", "author": {"login": "Alice"}, "committer": {"login": "Dave"}},
        ],
    )
    summary = build_executive_summary([pr_item], WINDOW)
    participants = summary["participants"]
    logins = [p["login"] for p in participants]
    assert logins == ["Alice", "Bob", "charlie", "Dave"]
    alice = next(p for p in participants if p["login"] == "Alice")
    assert "author" in alice["roles"]
    bob = next(p for p in participants if p["login"] == "Bob")
    assert "merged_by" in bob["roles"]
    charlie = next(p for p in participants if p["login"] == "charlie")
    assert "reviewer" in charlie["roles"]
    for p in participants:
        assert "rank" not in p
        assert "score" not in p
        assert "points" not in p


def test_executive_summary_partial_limitations_and_sanitization() -> None:
    from github_intelligence.report import build_executive_summary

    pr_inconsistent = pr(
        pull={"number": 30, "title": "Unsafe <script>alert(1)</script>"},
        consistent=False,
        limitations=["Falha na coleta de checks."],
    )
    summary = build_executive_summary([pr_inconsistent], WINDOW, report_complete=False)
    assert any("parcial" in lim.lower() for lim in summary["limitations"])


def test_executive_summary_empty_snapshots_has_zeroed_counts_and_safe_caveat() -> None:
    from github_intelligence.report import build_executive_summary

    summary = build_executive_summary([], WINDOW, report_complete=True)
    assert summary["available"] is True
    assert summary["report_complete"] is True
    assert summary["summary"]["total_prs_observed"] == 0
    assert summary["summary"]["merged_in_window_count"] == 0
    assert summary["summary"]["open_awaiting_review_count"] == 0
    assert summary["summary"]["open_drafts_count"] == 0
    assert summary["summary"]["potential_blockers_count"] == 0
    assert summary["merged_in_window"] == []
    assert summary["open_awaiting_review"] == []
    assert summary["open_drafts"] == []
    assert summary["potential_blockers"] == []
    assert summary["participants"] == []
    assert any("ausência total de atividade no GitHub" in lim for lim in summary["limitations"])
    assert "## 4. Participantes observados nas PRs coletadas (0)" in summary["markdown_text"]
    assert "_Nenhum participante observado nas PRs coletadas._" in summary["markdown_text"]


def test_executive_summary_participants_semantics_in_markdown() -> None:
    from github_intelligence.report import build_executive_summary

    pr_item = pr(
        pull={
            "number": 20,
            "user": {"login": "Alice"},
            "merged": True,
            "merged_at": "2026-10-18T10:00:00Z",
            "merged_by": {"login": "Bob"},
        },
    )
    summary = build_executive_summary([pr_item], WINDOW)
    md = summary["markdown_text"]
    assert "## 4. Participantes observados nas PRs coletadas (2)" in md
    assert "@Alice" in md
    assert "@Bob" in md
    assert any("participantes identificados" in lim.lower() for lim in summary["limitations"])


def test_executive_summary_draft_prs_segregated_from_awaiting_review() -> None:
    from github_intelligence.report import build_executive_summary

    pr_ready = pr(pull={"number": 10, "state": "open", "draft": False, "title": "Ready"})
    pr_draft_unreviewed = pr(pull={"number": 11, "state": "open", "draft": True, "title": "Draft Unreviewed"})
    pr_draft_approved = pr(
        pull={"number": 12, "state": "open", "draft": True, "title": "Draft Approved"},
        reviews=[
            {"state": "APPROVED", "user": {"login": "r1"}, "id": 1},
            {"state": "APPROVED", "user": {"login": "r2"}, "id": 2},
        ],
    )
    pr_ready_approved = pr(
        pull={"number": 13, "state": "open", "draft": False, "title": "Ready Approved"},
        reviews=[
            {"state": "APPROVED", "user": {"login": "r1"}, "id": 1},
            {"state": "APPROVED", "user": {"login": "r2"}, "id": 2},
        ],
    )

    summary = build_executive_summary(
        [pr_ready, pr_draft_unreviewed, pr_draft_approved, pr_ready_approved],
        WINDOW,
        repository_configs={"owner/repo": RepositoryConfig(full_name="owner/repo", required_approvals=2)},
    )

    awaiting_nums = [item["number"] for item in summary["open_awaiting_review"]]
    draft_nums = [item["number"] for item in summary["open_drafts"]]

    assert awaiting_nums == [10]
    assert 11 in draft_nums
    assert 12 in draft_nums
    assert 13 not in awaiting_nums
    assert 13 not in draft_nums

    # Counts
    assert summary["summary"]["open_awaiting_review_count"] == 1
    assert summary["summary"]["open_drafts_count"] == 2
    assert summary["summary"]["total_prs_observed"] == 4

    # Absence of duplication
    assert set(awaiting_nums).isdisjoint(set(draft_nums))


def test_executive_summary_draft_prs_with_potential_blockers() -> None:
    from github_intelligence.report import build_executive_summary

    pr_draft_blocked = pr(
        pull={"number": 21, "state": "open", "draft": True, "title": "Draft with Broken CI"},
        checks=[
            {"name": "test", "head_sha": "head", "status": "completed", "conclusion": "failure", "id": 1}
        ],
    )

    summary = build_executive_summary([pr_draft_blocked], WINDOW)
    draft_nums = [item["number"] for item in summary["open_drafts"]]
    awaiting_nums = [item["number"] for item in summary["open_awaiting_review"]]
    blocker_nums = [item["number"] for item in summary["potential_blockers"]]

    assert 21 in draft_nums
    assert 21 not in awaiting_nums
    assert 21 in blocker_nums
    blocker_item = next(b for b in summary["potential_blockers"] if b["number"] == 21)
    assert any("Verificações automatizadas (CI) com falha" in r for r in blocker_item["reasons"])


def test_executive_summary_blockers_neutral_and_accessible_language() -> None:
    from github_intelligence.report import build_executive_summary

    pr_ci = pr(
        pull={"number": 31, "state": "open", "title": "CI Fail"},
        statuses=[{"state": "failure", "context": "ci/tests", "sha": "head"}],
    )
    pr_mergeable_false = pr(
        pull={"number": 32, "state": "open", "title": "Mergeable False", "mergeable": False},
    )
    pr_dirty = pr(
        pull={"number": 33, "state": "open", "title": "Dirty", "mergeable": True, "mergeable_state": "dirty"},
    )
    pr_blocked = pr(
        pull={
            "number": 34,
            "state": "open",
            "title": "Blocked State",
            "mergeable": True,
            "mergeable_state": "blocked",
        },
    )
    pr_changes = pr(
        pull={"number": 35, "state": "open", "title": "Changes Req"},
        reviews=[{"state": "CHANGES_REQUESTED", "user": {"login": "rev"}, "id": 1}],
    )

    summary = build_executive_summary(
        [pr_ci, pr_mergeable_false, pr_dirty, pr_blocked, pr_changes],
        WINDOW,
    )
    blockers = {b["number"]: b["reasons"] for b in summary["potential_blockers"]}

    # CI
    assert any("Verificações automatizadas (CI) com falha (status: failing)." in r for r in blockers[31])
    # Mergeable False
    assert any("Conflito de mesclagem com a branch base (mergeable: false)." in r for r in blockers[32])
    # Dirty
    assert any(
        "Branch em conflito com a base impedindo mesclagem direta (mergeable_state: dirty)." in r
        for r in blockers[33]
    )
    # Blocked (Strictly neutral without guessing cause)
    assert any(
        "Mesclagem bloqueada ou restrita no GitHub (mergeable_state: blocked)." in r
        for r in blockers[34]
    )
    # Changes requested
    assert any(
        "Revisão com alterações solicitadas pelo revisor (CHANGES_REQUESTED)." in r
        for r in blockers[35]
    )


def test_executive_summary_multi_repository_and_markdown_formatting() -> None:
    from github_intelligence.report import build_executive_summary

    pr_fe_merged = pr(
        repository="org/frontend",
        pull={
            "number": 101,
            "title": "FE Login",
            "html_url": "https://github.com/org/frontend/pull/101",
            "merged": True,
            "merged_at": "2026-10-18T12:00:00Z",
            "merged_by": {"login": "lead"},
            "user": {"login": "dev1"},
        },
    )
    pr_be_awaiting = pr(
        repository="org/backend",
        pull={
            "number": 201,
            "state": "open",
            "draft": False,
            "title": "BE Auth",
            "html_url": "https://github.com/org/backend/pull/201",
            "user": {"login": "dev2"},
        },
    )
    pr_be_draft = pr(
        repository="org/backend",
        pull={
            "number": 202,
            "state": "open",
            "draft": True,
            "title": "BE Kafka",
            "html_url": "https://github.com/org/backend/pull/202",
            "user": {"login": "dev3"},
        },
    )
    pr_fe_blocked = pr(
        repository="org/frontend",
        pull={
            "number": 102,
            "state": "open",
            "draft": False,
            "title": "FE Profile",
            "html_url": "https://github.com/org/frontend/pull/102",
            "user": {"login": "dev1"},
        },
        statuses=[{"state": "failure", "context": "ci/tests", "sha": "head"}],
    )

    summary = build_executive_summary(
        [pr_fe_merged, pr_be_awaiting, pr_be_draft, pr_fe_blocked],
        WINDOW,
    )

    # Check that repository key is present in each item
    assert summary["merged_in_window"][0]["repository"] == "org/frontend"
    assert summary["open_awaiting_review"][0]["repository"] == "org/backend"
    assert summary["open_drafts"][0]["repository"] == "org/backend"
    assert summary["potential_blockers"][0]["repository"] == "org/frontend"

    md = summary["markdown_text"]

    # Check Markdown contains canonical repository tag on each section
    assert "[#101 FE Login](https://github.com/org/frontend/pull/101) · org/frontend" in md
    assert "[#201 BE Auth](https://github.com/org/backend/pull/201) · org/backend" in md
    assert "### Em preparação / rascunho (1)" in md
    assert "[#202 BE Kafka](https://github.com/org/backend/pull/202) · org/backend" in md
    assert "[#102 FE Profile](https://github.com/org/frontend/pull/102) · org/frontend" in md



def test_executive_summary_malicious_title_and_unknown_blocker_state() -> None:
    from github_intelligence.report import build_executive_summary

    pr_xss = pr(
        repository="org/frontend",
        pull={
            "number": 999,
            "title": "Fix <script>alert(1)</script> [test](link)",
            "state": "open",
            "draft": False,
            "mergeable": False,
            "mergeable_state": "unknown_custom_state",
        },
    )

    summary = build_executive_summary([pr_xss], WINDOW)
    blockers = summary["potential_blockers"]
    assert len(blockers) == 1
    assert blockers[0]["number"] == 999
    assert blockers[0]["repository"] == "org/frontend"
    assert any(
        "Conflito de mesclagem com a branch base (mergeable: false)." in r
        for r in blockers[0]["reasons"]
    )
    assert blockers[0]["mergeable_state"] == "unknown_custom_state"

    md = summary["markdown_text"]
    assert "· org/frontend" in md
    assert "<script>" not in md


def test_exec_link_url_validation_and_markdown_safety() -> None:
    from github_intelligence.report import _exec_link

    # Valid GitHub HTTPS URL
    valid = _exec_link(10, "feat: valid", "https://github.com/org/repo/pull/10")
    assert valid == "[#10 feat: valid](https://github.com/org/repo/pull/10)"

    # Malicious javascript: URL -> Plain text, no link
    js_url = _exec_link(11, "malicious js", "javascript:alert(1)")
    assert js_url == "#11 malicious js"
    assert "javascript" not in js_url
    assert "[" not in js_url and "](" not in js_url

    # Malicious data: URI -> Plain text, no link
    data_url = _exec_link(12, "data payload", "data:text/html;base64,PHNjcmlwdD4=")
    assert data_url == "#12 data payload"
    assert "data:" not in data_url

    # Non-permitted domain -> Plain text, no link
    evil_domain = _exec_link(13, "phishing", "https://evil.com/org/repo/pull/13")
    assert evil_domain == "#13 phishing"
    assert "evil.com" not in evil_domain

    # URL with credentials -> Plain text, no link
    creds_url = _exec_link(14, "creds", "https://user:pass@github.com/org/repo/pull/14")
    assert creds_url == "#14 creds"
    assert "user:pass" not in creds_url

    # Escapes brackets and HTML tags in title while keeping link
    escaped = _exec_link(15, "fix [core]: support <T> & 'quotes'", "https://github.com/org/repo/pull/15")
    assert escaped == "[#15 fix &#91;core&#93;: support &lt;T&gt; &amp; &#x27;quotes&#x27;](https://github.com/org/repo/pull/15)"


def test_executive_summary_blockers_draft_and_unknown_states_not_spurious_blockers() -> None:
    from github_intelligence.report import build_executive_summary

    # Draft with failing CI: in blockers, but ONLY with CI reason (no fake "draft" merge blocker)
    pr_draft_ci = pr(
        pull={
            "number": 40,
            "state": "open",
            "draft": True,
            "title": "Draft with CI fail",
            "mergeable": True,
            "mergeable_state": "draft",
        },
        statuses=[{"state": "failure", "context": "ci/tests", "sha": "head"}],
    )
    summary_ci = build_executive_summary([pr_draft_ci], WINDOW)
    assert len(summary_ci["potential_blockers"]) == 1
    assert len(summary_ci["open_drafts"]) == 1
    assert len(summary_ci["open_awaiting_review"]) == 0
    blocker_ci = summary_ci["potential_blockers"][0]
    assert blocker_ci["reasons"] == ["Verificações automatizadas (CI) com falha (status: failing)."]
    assert not any("draft" in r for r in blocker_ci["reasons"])

    # Draft without failures: NOT in blockers
    pr_draft_clean = pr(
        pull={
            "number": 41,
            "state": "open",
            "draft": True,
            "title": "Draft Clean",
            "mergeable": True,
            "mergeable_state": "draft",
        },
    )
    summary_clean = build_executive_summary([pr_draft_clean], WINDOW)
    assert len(summary_clean["potential_blockers"]) == 0
    assert len(summary_clean["open_drafts"]) == 1

    # Unknown mergeable_state with clean CI: NOT in blockers
    pr_unknown_clean = pr(
        pull={
            "number": 42,
            "state": "open",
            "draft": False,
            "title": "Computing Mergeability",
            "mergeable": None,
            "mergeable_state": "unknown",
        },
    )
    summary_unknown = build_executive_summary([pr_unknown_clean], WINDOW)
    assert len(summary_unknown["potential_blockers"]) == 0

    # Unknown mergeable_state with failing CI: in blockers, but ONLY with CI reason
    pr_unknown_ci = pr(
        pull={
            "number": 43,
            "state": "open",
            "draft": False,
            "title": "Unknown with CI fail",
            "mergeable": None,
            "mergeable_state": "unknown",
        },
        statuses=[{"state": "failure", "context": "ci/tests", "sha": "head"}],
    )
    summary_unknown_ci = build_executive_summary([pr_unknown_ci], WINDOW)
    assert len(summary_unknown_ci["potential_blockers"]) == 1
    blocker_u = summary_unknown_ci["potential_blockers"][0]
    assert blocker_u["reasons"] == ["Verificações automatizadas (CI) com falha (status: failing)."]
    assert not any("unknown" in r for r in blocker_u["reasons"])
