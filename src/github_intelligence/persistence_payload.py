"""Pure metadata allowlist for versioned PR persistence; never persist raw model dumps.

Titles, actor names, refs and collector-provided notes remain arbitrary text. This
boundary removes unapproved fields, not secrets embedded in approved text: it is
not universal DLP. No source code, bodies, diffs, messages or credentials have a
field in the persisted format.
"""

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from pydantic import AwareDatetime, TypeAdapter

from .models import Evidence, PullRequestSnapshot

_AWARE_DATE = TypeAdapter(AwareDatetime)

_ACTOR = {"id": "id", "login": "str"}
_BRANCH = {"sha": "str", "ref": "str"}
_SIGNATURE = {"name": "str", "date": "date"}
_APP = {"id": "id", "slug": "str", "name": "str"}
_PULL = {
    "number": "id",
    "title": "str",
    "state": "str",
    "draft": "bool",
    "created_at": "date",
    "updated_at": "date",
    "closed_at": "date",
    "merged_at": "date",
    "merged": "bool",
    "mergeable": "bool",
    "mergeable_state": "str",
    "commits": "count",
    "user": _ACTOR,
    "merged_by": _ACTOR,
    "head": _BRANCH,
    "base": _BRANCH,
    "html_url": "url",
}
_REVIEW = {
    "id": "id",
    "state": "str",
    "submitted_at": "date",
    "commit_id": "str",
    "user": _ACTOR,
    "html_url": "url",
}
_COMMIT = {
    "sha": "str",
    "html_url": "url",
    "author": _ACTOR,
    "committer": _ACTOR,
    "commit": {"author": _SIGNATURE, "committer": _SIGNATURE},
}
_CHECK = {
    "id": "id",
    "name": "str",
    "head_sha": "str",
    "status": "str",
    "conclusion": "str",
    "created_at": "date",
    "started_at": "date",
    "completed_at": "date",
    "app": _APP,
    "html_url": "url",
    "details_url": "url",
}
_STATUS = {
    "id": "id",
    "context": "str",
    "state": "str",
    "sha": "str",
    "created_at": "date",
    "updated_at": "date",
    "target_url": "url",
    "url": "url",
}


_EVIDENCE = {"kind": "str!", "url": "url!", "observed_at": "date!", "occurred_at": "date"}
_SNAPSHOT = {
    "repository": "str!",
    "pull": _PULL,
    "reviews": [_REVIEW],
    "commits": [_COMMIT],
    "checks": [_CHECK],
    "statuses": [_STATUS],
    "evidence": [_EVIDENCE],
    "limitations": ["str!"],
    "coverage_notes": ["str!"],
    "consistent": "bool!",
    "observed_at": "date!",
}


def _github_url(value: str) -> str | None:
    """Keep canonical GitHub HTTPS paths; strip query/fragment bearer material."""
    if any(ord(char) < 33 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or parts.netloc not in {"github.com", "api.github.com"}
            or not parts.path
            or re.search(r"%(?![0-9a-fA-F]{2})", parts.path)
        ):
            return None
        decoded_path = unquote(parts.path, errors="strict")
        if any(ord(char) < 32 or ord(char) == 127 for char in decoded_path) or "\\" in decoded_path:
            return None
        # Preserve existing escapes rather than turning %2F into %252F.
        path = quote(parts.path, safe="/:@!$&'*,;=+-._~%")
        return f"https://{parts.netloc}{path}"
    except (ValueError, UnicodeError):
        return None


def _record(value: Any, fields: dict[str, Any], *, restoring: bool) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("Expected a JSON object with string keys")
    if restoring and value.keys() - fields.keys():
        raise ValueError("Unknown persisted metadata fields")
    return {
        key: _value(value[key], spec, restoring=restoring) for key, spec in fields.items() if key in value
    }


def _value(value: Any, spec: Any, *, restoring: bool) -> Any:
    if isinstance(spec, list):
        if type(value) is not list:
            raise ValueError("Expected a JSON list")
        return [
            _record(item, spec[0], restoring=restoring)
            if isinstance(spec[0], dict)
            else _value(item, spec[0], restoring=restoring)
            for item in value
        ]
    if value is None:
        if isinstance(spec, str) and spec.endswith("!"):
            raise ValueError("Required metadata cannot be null")
        return None
    if isinstance(spec, dict):
        return _record(value, spec, restoring=restoring)
    kind = spec.removesuffix("!")
    if kind == "url":
        if type(value) is not str:
            raise ValueError("Expected URL string")
        cleaned = _github_url(value)
        if cleaned is None and spec.endswith("!"):
            raise ValueError("Invalid evidence URL")
        return cleaned
    if kind == "str":
        valid = type(value) is str
    elif kind == "bool":
        valid = type(value) is bool
    elif kind in {"id", "count"}:
        valid = type(value) is int and value >= (1 if kind == "id" else 0)
    elif kind == "date":
        if type(value) is not str and (restoring or not isinstance(value, datetime)):
            raise ValueError("Expected an ISO timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if type(value) is str else value
        _AWARE_DATE.validate_python(parsed)
        return value.isoformat() if isinstance(value, datetime) else value
    else:
        raise ValueError("Unsupported metadata field")
    if not valid:
        raise ValueError(f"Invalid {kind} metadata value")
    return value


def _normalize(value: Any, *, restoring: bool) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != _SNAPSHOT.keys():
        raise ValueError("Expected exact snapshot fields")
    if type(value["pull"]) is not dict:
        raise ValueError("Expected pull object")
    for item in value["evidence"] if type(value["evidence"]) is list else []:
        if type(item) is not dict or item.keys() != _EVIDENCE.keys():
            raise ValueError("Expected exact evidence fields")
    result = _record(value, _SNAPSHOT, restoring=restoring)
    for evidence in result["evidence"]:
        Evidence.model_validate(evidence)
    return result


def restore_snapshot(payload: dict[str, Any]) -> PullRequestSnapshot:
    """Validate exact v1 metadata, then restore through Pydantic; never fetch or write."""
    if type(payload) is not dict or payload.keys() != {"schema_version", "snapshot"}:
        raise ValueError("Expected exact payload envelope")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported snapshot schema_version")
    return PullRequestSnapshot.model_validate(_normalize(payload["snapshot"], restoring=True))


def snapshot_payload(snapshot: PullRequestSnapshot) -> dict[str, Any]:
    """Copy only approved metadata, leaving the ephemeral raw snapshot untouched."""
    return {
        "schema_version": 1,
        "snapshot": _normalize(
            {
                "repository": snapshot.repository,
                "pull": snapshot.pull,
                "reviews": snapshot.reviews,
                "commits": snapshot.commits,
                "checks": snapshot.checks,
                "statuses": snapshot.statuses,
                "evidence": [
                    {
                        "kind": item.kind,
                        "url": item.url,
                        "observed_at": item.observed_at,
                        "occurred_at": item.occurred_at,
                    }
                    for item in snapshot.evidence
                ],
                "limitations": snapshot.limitations,
                "coverage_notes": snapshot.coverage_notes,
                "consistent": snapshot.consistent,
                "observed_at": snapshot.observed_at,
            },
            restoring=False,
        ),
    }
