"""Deterministic interpretation of observations, never a branch-policy oracle."""

from datetime import datetime
from typing import Any

from .models import Assessment, Evidence, PullRequestSnapshot, RepositoryConfig


def _instant(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else float("-inf")
    except (ValueError, TypeError, OverflowError):
        return float("-inf")


def _order(item: dict[str, Any]) -> tuple[float, int]:
    return (_instant(item.get("submitted_at") or item.get("created_at")), int(item.get("id") or 0))


def _reviews(snapshot: PullRequestSnapshot, config: RepositoryConfig) -> tuple[str, int, list[str]]:
    author = str((snapshot.pull.get("user") or {}).get("login") or "").casefold()
    decisions: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    missing_identity = False
    for item in sorted(snapshot.reviews, key=_order):
        login = str((item.get("user") or {}).get("login") or "").casefold()
        state = item.get("state")
        if not login and state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            missing_identity = True
        if login and login != author and state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            decisions[login] = item
    count = sum(item.get("state") == "APPROVED" for item in decisions.values())
    if any(item.get("state") == "DISMISSED" for item in snapshot.reviews):
        reasons.append("Review DISMISSED não conta; timestamps e histórico não reconstroem toda a decisão.")
    head = (snapshot.pull.get("head") or {}).get("sha")
    if any(item.get("commit_id") != head for item in decisions.values()):
        reasons.append("Review em SHA antigo ou desconhecido: validade depende de regras não comprovadas.")
    if any(item.get("state") == "CHANGES_REQUESTED" for item in decisions.values()):
        state = "changes_requested"
        reasons.append("Alterações solicitadas observadas; resolver e conferir a política de revisão.")
    elif count == 0:
        state = "no_approvals"
    elif count < config.required_approvals:
        state = "awaiting_approvals"
    else:
        state = "approved_observed"
    if missing_identity:
        reasons.append("Review sem identidade de revisor: decisão atual não comprovada.")
    if missing_identity or not snapshot.consistent or snapshot.limitations:
        state = "unknown"
        reasons.append("Snapshot incompleto ou inconsistente: contagens apenas observadas, não confirmadas.")
    return state, count, reasons


def _ci(snapshot: PullRequestSnapshot) -> tuple[str, list[str]]:
    head = (snapshot.pull.get("head") or {}).get("sha")
    reasons: list[str] = []
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    unknown_sha = False
    for kind, items, sha_key in (
        ("status", snapshot.statuses, "sha"),
        ("check", snapshot.checks, "head_sha"),
    ):
        for item in items:
            sha = item.get(sha_key)
            if not head or not sha:
                unknown_sha = True
                continue
            if sha != head:
                continue
            app = item.get("app") or {}
            app_key = str(app.get("id") or app.get("slug") or app.get("name") or "unknown")
            key = (
                kind,
                str(item.get("context" if kind == "status" else "name", "unknown")),
                app_key if kind == "check" else "",
            )

            def order(row: dict[str, Any]) -> tuple[float, int]:
                return (
                    _instant(row.get("started_at") or row.get("created_at") or row.get("completed_at")),
                    int(row.get("id") or 0),
                )

            if key not in selected or order(item) > order(selected[key]):
                selected[key] = item
    states: list[str] = []
    for (kind, _, _), item in selected.items():
        if kind == "status":
            state = {
                "success": "passing",
                "failure": "failing",
                "error": "failing",
                "pending": "pending",
            }.get(str(item.get("state")), "unknown")
        elif item.get("status") != "completed":
            state = (
                "pending"
                if item.get("status") in {"queued", "in_progress", "waiting", "pending", "requested"}
                else "unknown"
            )
        else:
            conclusion = item.get("conclusion")
            state = (
                "passing"
                if conclusion == "success"
                else (
                    "failing"
                    if conclusion
                    in {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
                    else "unknown"
                )
            )
            if conclusion in {"neutral", "skipped"}:
                reasons.append("Check neutral/skipped: sem política não equivale a aprovação de CI.")
        states.append(state)
    if selected:
        reasons.append("CI observado no head atual; checks obrigatórios e opcionais não foram determinados.")
    if unknown_sha:
        reasons.append("CI sem SHA verificável não comprova o head atual.")
    if not snapshot.consistent or snapshot.limitations:
        return "unknown", reasons
    if "failing" in states:
        return "failing", reasons
    if unknown_sha or "unknown" in states:
        return "unknown", reasons
    if "pending" in states:
        return "pending", reasons
    return ("passing" if states else "not_observed"), reasons


def evaluate(snapshot: PullRequestSnapshot, config: RepositoryConfig) -> Assessment:
    pull = snapshot.pull
    number = int(pull.get("number", 0))
    url = f"https://github.com/{config.full_name}/pull/{number}"
    review, count, reasons = _reviews(snapshot, config)
    ci, ci_reasons = _ci(snapshot)
    reasons.extend(ci_reasons)
    merged = bool(pull.get("merged") or pull.get("merged_at"))
    lifecycle = (
        "merged"
        if merged
        else ("closed" if pull.get("state") == "closed" else ("draft" if pull.get("draft") else "open"))
    )
    integration = "unknown"
    base = (pull.get("base") or {}).get("ref")
    if merged and config.staging_branch and base:
        matches = "corresponde" if base == config.staging_branch else "não corresponde"
        reasons.append(
            f"A base atual {matches} à homologação configurada; "
            "destino histórico do merge não comprovado. Integração permanece desconhecida."
        )
    blocked = False
    if lifecycle == "draft":
        blocked = True
        reasons.append("Rascunho: concluir antes de solicitar merge.")
    if pull.get("mergeable") is False or pull.get("mergeable_state") in {"blocked", "dirty", "draft"}:
        blocked = True
        reasons.append("Conflito ou mergeable_state bloqueado observado; conferir no GitHub.")
    if review == "changes_requested" or ci == "failing":
        reasons.append(
            "Pendência observada; sem regras efetivas e permissões não se confirma que ela bloqueia merge."
        )
    eligibility = "blocked" if blocked else "unknown"
    if lifecycle in {"merged", "closed"}:
        eligibility = "unknown"
    if not snapshot.consistent or snapshot.limitations:
        eligibility = "unknown"
    reasons.extend(snapshot.coverage_notes)
    reasons.extend(snapshot.limitations)
    return Assessment(
        number=number,
        title=str(pull.get("title", "")),
        url=url,
        lifecycle=lifecycle,
        review=review,
        ci=ci,
        approvals_observed=count,
        merge_eligibility=eligibility,
        integration=integration,
        reasons=["Regras de proteção, permissões e CODEOWNERS não comprovados.", *reasons],
        evidence=[*snapshot.evidence, Evidence(kind="pull", url=url, observed_at=snapshot.observed_at)],
        verified_at=snapshot.observed_at,
    )
