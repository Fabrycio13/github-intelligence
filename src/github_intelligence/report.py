"""Portuguese Markdown with period events separated from current observations."""

import html
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from .evaluate import evaluate
from .models import Assessment, PullRequestSnapshot, ReportWindow, RepositorySnapshot


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return "".join(
        f"&#{ord(char)};" if char in "`*_{}[]()#!|~" or char == chr(92) else html.escape(char, quote=True)
        for char in " ".join(str(value).split())
    )


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or any(ord(char) < 33 for char in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or parts.hostname not in {"github.com", "api.github.com"}
            or parts.username
            or parts.password
            or parts.port
            or not parts.path
        ):
            return None
    except ValueError:
        return None
    return quote(value, safe=":/?#@!$&'*,;=%+-._~")


def _link(label: Any, value: Any, fallback: str | None = None) -> str:
    url = _url(value) or _url(fallback)
    return f"[{_text(label)}]({url})" if url else _text(label)


def _events(snapshot: PullRequestSnapshot, assessment: Assessment, window: ReportWindow) -> list[str]:
    events: list[tuple[str, str]] = []

    def add(label: str, timestamp: Any, url: Any = None) -> None:
        if not isinstance(timestamp, (str, datetime)) or not window.contains(timestamp):
            return
        instant = (
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if isinstance(timestamp, str)
            else timestamp
        )
        stamp = _utc(instant)
        events.append((stamp, f"- {stamp} — {_link(label, url, assessment.url)}"))

    pull = snapshot.pull
    for key, label in (
        ("created_at", "PR criado"),
        ("merged_at", "PR integrado"),
        ("closed_at", "PR fechado"),
    ):
        add(f"{label} #{assessment.number}", pull.get(key), assessment.url)
    for review in snapshot.reviews:
        person = (review.get("user") or {}).get("login", "desconhecido")
        label = (
            f"Review submetido #{assessment.number} por {person} "
            f"(estado observado: {review.get('state', 'unknown')})"
        )
        add(label, review.get("submitted_at"), review.get("html_url"))
    for commit in snapshot.commits:
        committer = (commit.get("commit") or {}).get("committer") or {}
        add(
            f"Commit registrado #{assessment.number} {commit.get('sha', '')}",
            committer.get("date"),
            commit.get("html_url"),
        )
    return [line for _, line in sorted(set(events))]


def _current(snapshot: PullRequestSnapshot, assessment: Assessment) -> list[str]:
    pull = snapshot.pull
    author = (pull.get("user") or {}).get("login", "desconhecido")
    merger = (pull.get("merged_by") or {}).get("login", "não observado")
    reviewers = sorted(
        {
            str((item.get("user") or {}).get("login"))
            for item in snapshot.reviews
            if (item.get("user") or {}).get("login")
        },
        key=str.casefold,
    )
    lines = [
        f"- {_link(f'#{assessment.number} {assessment.title}', assessment.url)}",
        f"  - Verificado em {_utc(assessment.verified_at)} UTC; ciclo: {assessment.lifecycle}; "
        f"revisão: {assessment.review}; CI: {assessment.ci}; merge: {assessment.merge_eligibility}.",
        f"  - Aprovações observadas: {assessment.approvals_observed}; confirmadas: desconhecidas.",
        f"  - Integração: {assessment.integration}; deploy: unknown; validação funcional: unknown.",
        f"  - Pessoas (descritivo): autor: {_text(author)}; "
        f"revisores: {_text(', '.join(reviewers) or 'não observados')}; "
        f"merge por: {_text(merger)}.",
    ]
    for reason in dict.fromkeys(assessment.reasons):
        lines.append(f"  - {_text(reason)}")
    evidence = list(dict.fromkeys(_link(item.kind, item.url) for item in assessment.evidence))
    lines.append("  - Evidências: " + "; ".join(evidence))
    return lines


def render_report(snapshots: list[RepositorySnapshot], window: ReportWindow) -> str:
    lines = [
        "# GitHub — relatório de evidências",
        "",
        f"Intervalo [{_utc(window.since)}, {_utc(window.until)}) UTC",
        "",
        "Eventos no período não são estados históricos: estados abaixo são os observados na coleta.",
        "",
    ]
    if any(not item.complete or item.limitations for item in snapshots):
        lines.extend(["**RELATÓRIO PARCIAL — há fontes indisponíveis ou snapshots inconsistentes.**", ""])
    questions: list[str] = []
    if not snapshots:
        lines.extend(["Nenhum repositório fornecido; não há cobertura de atividade.", ""])
    for repository in sorted(snapshots, key=lambda item: item.config.full_name.casefold()):
        auth = "GitHub App (autenticado)" if repository.authenticated else "acesso público (sem autenticação)"
        lines.extend(
            [
                f"## {_text(repository.config.full_name)}",
                f"Observado em {_utc(repository.observed_at)} UTC; {auth}.",
            ]
        )
        incomplete = not repository.complete or bool(repository.limitations)
        if incomplete:
            lines.append("**INCOMPLETO — falha/limitação de coleta; não concluir ausência nem prontidão.**")
            questions.append("Qual fonte de coleta precisa ser recuperada antes de decidir?")
        lines.extend(f"- Limitação: {_text(item)}" for item in repository.limitations)
        lines.extend(f"- Cobertura da POC: {_text(item)}" for item in repository.coverage_notes)
        if not repository.pulls:
            lines.extend(["Nenhum PR retornado; isso não comprova ausência de atividade.", ""])
            continue
        assessments: list[tuple[PullRequestSnapshot, Assessment]] = []
        for snapshot in sorted(repository.pulls, key=lambda item: int(item.pull.get("number", 0))):
            effective = snapshot
            if incomplete:
                effective = snapshot.model_copy(
                    update={
                        "consistent": False,
                        "limitations": [
                            *snapshot.limitations,
                            *repository.limitations,
                            "Coleta do repositório incompleta.",
                        ],
                    }
                )
            if not snapshot.consistent or snapshot.limitations:
                lines.append(
                    f"**INCOMPLETO — PR #{int(snapshot.pull.get('number', 0))}: "
                    "snapshot limitado/inconsistente.**"
                )
                questions.append("Qual fonte de coleta precisa ser recuperada antes de decidir?")
            assessments.append((snapshot, evaluate(effective, repository.config)))
        lines.extend(["", "### Eventos no intervalo"])
        events = sorted(
            {event for snapshot, assessment in assessments for event in _events(snapshot, assessment, window)}
        )
        lines.extend(
            events or ["Nenhum evento tipado observado no intervalo; não comprova ausência de atividade."]
        )
        lines.extend(["", "### Estados atuais (não restritos ao intervalo)"])
        for snapshot, assessment in assessments:
            lines.extend(_current(snapshot, assessment))
            if assessment.lifecycle in {"open", "draft"}:
                if assessment.ci == "failing":
                    questions.append(
                        "Quem verificará as falhas de CI observadas e quais checks são obrigatórios?"
                    )
                if assessment.review in {"no_approvals", "awaiting_approvals", "changes_requested"}:
                    questions.append("Quem pode revisar as pendências e confirmar permissões/CODEOWNERS?")
                questions.append("Quais regras de proteção e evidências faltam para decidir sobre merge?")
        lines.append("")
    lines.extend(["## Perguntas do dia"])
    lines.extend(f"- {question}" for question in list(dict.fromkeys(questions))[:3])
    if not questions:
        lines.append("Nenhuma pergunta adicional sustentada pela amostra.")
    lines.extend(
        [
            "",
            "## Limitações",
            "- Aprovações são observadas, não confirmadas: permissões, CODEOWNERS, "
            "regras de proteção e checks obrigatórios não comprovados. O limiar configurado "
            "não substitui a política do GitHub; nenhum merge é confirmado elegível.",
            "- Reviews em SHA antigo exigem conferir a política, sem invalidação automática. "
            "COMMENTED/PENDING não apagam decisões. DISMISSED não aprova; timestamps de reviews "
            "e histórico de dispensas não permitem reconstrução completa.",
            "- CI refere-se somente ao head observado; falhas podem ser opcionais. "
            "Neutral/skipped não comprovam sucesso sem política.",
            "- A base atual é apenas contexto; o destino histórico do merge "
            "permanece desconhecido sem evidência. "
            "Merge não prova deploy ou validação funcional; ambos desconhecidos. "
            "Sem validação de código-fonte, execução funcional ou ambiente nesta POC.",
            "- Eventos usam created_at, merged_at, closed_at, submitted_at e data do committer; "
            "updated_at não prova tipo de evento. Commit registrado não prova push no intervalo; "
            "amostra de PRs não representa toda atividade do repositório.",
            "- Pessoas são descritivas, sem ranking ou inferência de produtividade. "
            "Corpos e diffs não são incluídos.",
        ]
    )
    return "\n".join(lines) + "\n"



def _record_participant(
    participants_map: dict[str, dict[str, Any]],
    login: str,
    role: str,
    pr_number: int,
) -> None:
    cleaned = login.strip()
    if not cleaned:
        return
    key = cleaned.casefold()
    if key not in participants_map:
        participants_map[key] = {
            "login": cleaned,
            "roles": set(),
            "pr_numbers": set(),
        }
    participants_map[key]["roles"].add(role)
    participants_map[key]["pr_numbers"].add(pr_number)


def _exec_link(number: int, title: str, url: str) -> str:
    safe_title = html.escape(str(title)).replace("[", "&#91;").replace("]", "&#93;")
    safe_url = _url(url)
    if not safe_url:
        return f"#{number} {safe_title}"
    return f"[#{number} {safe_title}]({safe_url})"


def render_executive_markdown(summary: dict[str, Any]) -> str:
    window = summary.get("window", {})
    since = window.get("since", "—")
    until = window.get("until", "—")
    lines = [
        "# Relatório Executivo Semanal",
        "",
        f"**Janela UTC:** `[{since}, {until})`",
        "",
    ]
    if not summary.get("report_complete", True):
        lines.extend([
            "> **Aviso:** Relatório parcial — fontes indisponíveis ou snapshots inconsistentes no período.",
            "",
        ])

    merged = summary.get("merged_in_window", [])
    lines.append(f"## 1. Integradas no período ({len(merged)})")
    if not merged:
        lines.append("_Nenhuma PR integrada observada dentro desta janela temporal._")
    else:
        for pr in merged:
            link = _exec_link(pr['number'], pr['title'], pr['url'])
            lines.append(
                f"- {link} · {pr['repository']} "
                f"(autor: @{pr['author']}, merge por: @{pr['merged_by']} em {pr['merged_at']})"
            )
    lines.append("")

    awaiting = summary.get("open_awaiting_review", [])
    lines.append(f"## 2. Aguardando revisão ({len(awaiting)})")
    if not awaiting:
        lines.append("_Nenhuma PR aberta aguardando aprovações._")
    else:
        for pr in awaiting:
            link = _exec_link(pr['number'], pr['title'], pr['url'])
            lines.append(
                f"- {link} · {pr['repository']} "
                f"(autor: @{pr['author']} · aprovações observadas: "
                f"{pr['approvals_observed']}/{pr['expected_approvals']})"
            )
    lines.append("")

    drafts = summary.get("open_drafts", [])
    if drafts:
        lines.append(f"### Em preparação / rascunho ({len(drafts)})")
        for pr in drafts:
            link = _exec_link(pr['number'], pr['title'], pr['url'])
            lines.append(
                f"- {link} · {pr['repository']} "
                f"(autor: @{pr['author']} · em preparação)"
            )
        lines.append("")

    blockers = summary.get("potential_blockers", [])
    lines.append(f"## 3. Possíveis bloqueios técnicos ({len(blockers)})")
    if not blockers:
        lines.append("_Nenhum bloqueio técnico crítico observado nas PRs ativas._")
    else:
        for pr in blockers:
            reasons = "; ".join(pr.get("reasons", [])) or "Pendência observada."
            link = _exec_link(pr['number'], pr['title'], pr['url'])
            lines.append(
                f"- {link} · {pr['repository']} "
                f"(autor: @{pr['author']}): {reasons}"
            )
    lines.append("")

    participants = summary.get("participants", [])
    lines.append(f"## 4. Participantes observados nas PRs coletadas ({len(participants)})")
    if not participants:
        lines.append("_Nenhum participante observado nas PRs coletadas._")
    else:
        for p in participants:
            roles = ", ".join(p.get("roles", []))
            prs = ", ".join(f"#{num}" for num in p.get("pr_numbers", []))
            lines.append(f"- **@{p['login']}**: {roles} (PRs: {prs})")
    lines.append("")

    lines.append("## 5. Limitações e Fronteiras dos Dados")
    for lim in summary.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)


def build_executive_summary(
    snapshots: list[PullRequestSnapshot],
    window: ReportWindow,
    *,
    repository_configs: dict[str, Any] | None = None,
    report_complete: bool = True,
) -> dict[str, Any]:
    from .models import RepositoryConfig

    configs = repository_configs or {}
    assessments: list[tuple[PullRequestSnapshot, Assessment, RepositoryConfig]] = []
    for snap in snapshots:
        raw_cfg = configs.get(snap.repository)
        cfg = (
            raw_cfg
            if isinstance(raw_cfg, RepositoryConfig)
            else RepositoryConfig(full_name=snap.repository)
        )
        assessments.append((snap, evaluate(snap, cfg), cfg))

    merged_in_window: list[dict[str, Any]] = []
    open_awaiting_review: list[dict[str, Any]] = []
    open_drafts: list[dict[str, Any]] = []
    potential_blockers: list[dict[str, Any]] = []
    participants_map: dict[str, dict[str, Any]] = {}

    for snap, assessment, cfg in assessments:
        pull = snap.pull
        merged_at = pull.get("merged_at")
        is_merged = bool(pull.get("merged") or merged_at)

        # 1. Merged in window: only if merged_at is within [since, until)
        if is_merged and merged_at and window.contains(merged_at):
            merged_in_window.append({
                "number": assessment.number,
                "title": assessment.title,
                "url": assessment.url,
                "repository": snap.repository,
                "merged_at": merged_at,
                "author": (pull.get("user") or {}).get("login") or "desconhecido",
                "merged_by": (pull.get("merged_by") or {}).get("login") or "desconhecido",
                "base_branch": (pull.get("base") or {}).get("ref") or "desconhecido",
            })

        # 2. Open awaiting review and drafts in preparation
        if assessment.lifecycle in {"open", "draft"}:
            is_draft = bool(pull.get("draft") or assessment.lifecycle == "draft")
            item_data = {
                "number": assessment.number,
                "title": assessment.title,
                "url": assessment.url,
                "repository": snap.repository,
                "created_at": pull.get("created_at"),
                "author": (pull.get("user") or {}).get("login") or "desconhecido",
                "is_draft": is_draft,
                "approvals_observed": assessment.approvals_observed,
                "expected_approvals": cfg.required_approvals,
                "review_state": assessment.review,
            }
            if is_draft:
                open_drafts.append(item_data)
            elif assessment.review in {"no_approvals", "awaiting_approvals"}:
                open_awaiting_review.append(item_data)

        # 3. Potential blockers: active open/draft PRs with failing CI, conflicts or changes requested
        if assessment.lifecycle in {"open", "draft"}:
            has_ci_failure = assessment.ci == "failing"
            has_conflict = (
                pull.get("mergeable") is False
                or pull.get("mergeable_state") in {"blocked", "dirty"}
            )
            has_changes_requested = assessment.review == "changes_requested"
            if has_ci_failure or has_conflict or has_changes_requested:
                reasons: list[str] = []
                if has_ci_failure:
                    reasons.append("Verificações automatizadas (CI) com falha (status: failing).")
                if pull.get("mergeable") is False:
                    reasons.append("Conflito de mesclagem com a branch base (mergeable: false).")
                elif pull.get("mergeable_state") == "dirty":
                    reasons.append(
                        "Branch em conflito com a base impedindo mesclagem direta (mergeable_state: dirty)."
                    )
                elif pull.get("mergeable_state") == "blocked":
                    reasons.append(
                        "Mesclagem bloqueada ou restrita no GitHub (mergeable_state: blocked)."
                    )
                if has_changes_requested:
                    reasons.append("Revisão com alterações solicitadas pelo revisor (CHANGES_REQUESTED).")
                potential_blockers.append({
                    "number": assessment.number,
                    "title": assessment.title,
                    "url": assessment.url,
                    "repository": snap.repository,
                    "author": (pull.get("user") or {}).get("login") or "desconhecido",
                    "ci_state": assessment.ci,
                    "mergeable_state": (
                        pull.get("mergeable_state")
                        or ("conflicting" if pull.get("mergeable") is False else "unknown")
                    ),
                    "review_state": assessment.review,
                    "reasons": reasons,
                })

        # 4. Participants from real evidence
        author = (pull.get("user") or {}).get("login")
        if author:
            _record_participant(participants_map, author, "author", assessment.number)
        merged_by = (pull.get("merged_by") or {}).get("login")
        if merged_by and is_merged:
            _record_participant(participants_map, merged_by, "merged_by", assessment.number)
        for review in snap.reviews:
            rev_user = (review.get("user") or {}).get("login")
            if rev_user:
                _record_participant(participants_map, rev_user, "reviewer", assessment.number)
        for commit in snap.commits:
            c_auth = (commit.get("author") or {}).get("login")
            c_comm = (commit.get("committer") or {}).get("login")
            for login in (c_auth, c_comm):
                if login:
                    _record_participant(participants_map, login, "committer", assessment.number)

    merged_in_window.sort(key=lambda item: (str(item.get("merged_at", "")), item["number"]), reverse=True)
    open_awaiting_review.sort(key=lambda item: item["number"], reverse=True)
    open_drafts.sort(key=lambda item: item["number"], reverse=True)
    potential_blockers.sort(key=lambda item: item["number"], reverse=True)

    participants = [
        {
            "login": item["login"],
            "roles": sorted(item["roles"]),
            "pr_numbers": sorted(item["pr_numbers"]),
        }
        for item in sorted(participants_map.values(), key=lambda p: p["login"].casefold())
    ]

    limitations = [
        (
            "Merges na branch base indicam integração no repositório, "
            "mas não comprovam deploy em produção nem validação funcional."
        ),
        (
            "Aprovações observadas refletem a expectativa de revisão "
            "configurada localmente, não regras comprovadas do GitHub."
        ),
        (
            "Contagens de PRs e participações não constituem métricas "
            "de produtividade individual nem dedicação do colaborador."
        ),
        (
            "Amostra restrita às PRs observadas; participantes identificados "
            "refletem pessoas observadas nas PRs coletadas, sem comprovação "
            "de atividade no período temporal nem ausência total de atividade no GitHub."
        ),
    ]
    has_partial = not report_complete or any(
        not s.consistent or bool(s.limitations) for s in snapshots
    )
    if has_partial:
        limitations.insert(
            0,
            "Relatório parcial: há fontes indisponíveis ou snapshots inconsistentes no período.",
        )

    result: dict[str, Any] = {
        "available": True,
        "report_complete": not has_partial,
        "window": {
            "since": _utc(window.since),
            "until": _utc(window.until),
        },
        "summary": {
            "total_prs_observed": len(snapshots),
            "merged_in_window_count": len(merged_in_window),
            "open_awaiting_review_count": len(open_awaiting_review),
            "open_drafts_count": len(open_drafts),
            "potential_blockers_count": len(potential_blockers),
        },
        "merged_in_window": merged_in_window,
        "open_awaiting_review": open_awaiting_review,
        "open_drafts": open_drafts,
        "potential_blockers": potential_blockers,
        "participants": participants,
        "limitations": limitations,
    }
    result["markdown_text"] = render_executive_markdown(result)
    return result
