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
