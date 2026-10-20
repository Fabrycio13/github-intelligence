"""GitHub App settings from environment or nonsecret local references."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from github_intelligence.secrets import SecretError, load_private_key


class ConfigurationError(Exception):
    """User-actionable message without echoing secret input."""


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError
        result[name] = value
    return result


@dataclass(frozen=True)
class AppSettings:
    app_id: str
    installation_id: int
    private_key: bytes = field(repr=False)

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None, *, config_path: Path | None = None,
    ) -> "AppSettings":
        env = os.environ if values is None else values
        names = ("GITHUB_APP_ID", "GITHUB_INSTALLATION_ID", "GITHUB_PRIVATE_KEY_PATH")
        try:
            local: dict[str, str | int] = {}
            if config_path is not None and config_path.exists():
                parsed = json.loads(
                    config_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields,
                )
                if not isinstance(parsed, dict) or set(parsed) != {
                    "app_id", "installation_id", "private_key_path",
                }:
                    raise ValueError
                if (
                    not isinstance(parsed["app_id"], str)
                    or not parsed["app_id"].isascii()
                    or not parsed["app_id"].isdecimal()
                    or int(parsed["app_id"]) <= 0
                    or type(parsed["installation_id"]) is not int
                    or parsed["installation_id"] <= 0
                    or not isinstance(parsed["private_key_path"], str)
                    or not parsed["private_key_path"].strip()
                ):
                    raise ValueError
                local = parsed
            app_value = env.get(names[0]) or local.get("app_id")
            installation_value = env.get(names[1]) or local.get("installation_id")
            key_value = env.get(names[2]) or local.get("private_key_path")
            if (
                not isinstance(app_value, (str, int))
                or not isinstance(installation_value, (str, int))
                or not isinstance(key_value, str)
            ):
                raise ValueError
            app_id = int(app_value)
            installation_id = int(installation_value)
            if app_id <= 0 or installation_id <= 0:
                raise ValueError
            key_path = Path(key_value)
            if not env.get(names[2]) and config_path is not None and not key_path.is_absolute():
                key_path = config_path.parent / key_path
            key = load_private_key(key_path)
            return cls(app_id=str(app_id), installation_id=installation_id, private_key=key)
        except (ValueError, OSError, SecretError):
            raise ConfigurationError(
                "Configuração da GitHub App inválida ou chave privada inacessível."
            ) from None
