"""Restricted production database settings and connection lifecycle."""

import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from github_intelligence.config import ConfigurationError
from github_intelligence.secrets import SecretError, load_private_key
from github_intelligence.storage import StorageError

_CONFIG_ERROR = "Database configuration is invalid or its encrypted password/CA file is inaccessible."
_FIELDS = {"host", "user", "project_ref", "port", "password_path", "sslrootcert"}
_WORKER = "github_intelligence_worker"
_POOLER = re.compile(r"aws-[0-9]+-[a-z]+(?:-[a-z0-9]+)+\.pooler\.supabase\.com")


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError
    value.encode("utf-8", errors="strict")
    return value


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    user: str
    project_ref: str
    password: str = field(repr=False)
    port: int = 5432
    sslrootcert: str = "system"

    def __post_init__(self) -> None:
        try:
            host = _text(self.host)
            user = _text(self.user)
            ref = _text(self.project_ref)
            _text(self.password)
            ca = _text(self.sslrootcert)
            if not re.fullmatch(r"[a-z]{20}", ref) or type(self.port) is not int or self.port != 5432:
                raise ValueError
            if host == f"db.{ref}.supabase.co":
                if user != _WORKER:
                    raise ValueError
            elif _POOLER.fullmatch(host):
                if user != f"{_WORKER}.{ref}":
                    raise ValueError
            else:
                raise ValueError
            if ca != "system" and not Path(ca).is_file():
                raise ValueError
        except (ValueError, OSError):
            raise ConfigurationError(_CONFIG_ERROR) from None

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None, *, config_path: Path | None = None,
    ) -> "DatabaseSettings":
        """Resolve explicit environment overrides over a strictly nonsecret JSON file."""
        env = os.environ if values is None else values
        try:
            local: dict[str, object] = {}
            if config_path is not None:
                parsed = json.loads(config_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
                if not isinstance(parsed, dict) or not set(parsed).issubset(_FIELDS):
                    raise ValueError
                for key, value in parsed.items():
                    if key == "port":
                        if type(value) is not int or value != 5432:
                            raise ValueError
                    else:
                        _text(value)
                local = parsed

            def setting(name: str, default: str = "") -> str:
                return _text(env.get(f"GHI_DATABASE_{name.upper()}", local.get(name, default)))

            ca = setting("sslrootcert", "system")
            if ca != "system" and config_path is not None and "GHI_DATABASE_SSLROOTCERT" not in env:
                ca = str((config_path.parent / ca).resolve())
            if "GHI_DATABASE_PASSWORD" in env:
                password = _text(env["GHI_DATABASE_PASSWORD"])
            else:
                path = Path(setting("password_path"))
                # The shared byte reader also accepts plaintext; never permit that branch here.
                if path.suffix != ".dpapi":
                    raise ValueError
                if config_path is not None and "GHI_DATABASE_PASSWORD_PATH" not in env:
                    path = config_path.parent / path
                password = load_private_key(path).decode("utf-8", errors="strict")
            return cls(
                host=setting("host"), user=setting("user"), project_ref=setting("project_ref"),
                password=password, sslrootcert=ca,
            )
        except (ValueError, OSError, SecretError):
            raise ConfigurationError(_CONFIG_ERROR) from None


@contextmanager
def connect_database(settings: DatabaseSettings) -> Iterator[psycopg.Connection[Any]]:
    """Open a verified-TLS worker connection, close it on every exit, and hide driver details."""
    try:
        connection = psycopg.connect(
            host=settings.host, user=settings.user, password=settings.password,
            port=5432, dbname="postgres", sslmode="verify-full", sslrootcert=settings.sslrootcert,
            autocommit=True, prepare_threshold=None, connect_timeout=10,
            application_name="github-intelligence", options="", passfile=os.devnull,
        )
        try:
            yield connection
        finally:
            connection.close()
    except psycopg.Error as error:
        # libpq connection errors may have no SQLSTATE. Match known categories
        # internally, but never echo provider diagnostics or connection values.
        detail = str(error).lower()
        if error.sqlstate == "28P01" or "password authentication failed" in detail:
            message = "AUTHENTICATION_REJECTED: senha recusada para o login configurado."
        elif "certificate verify failed" in detail or "root certificate" in detail:
            message = "TLS_CERTIFICATE_FAILED: certificado CA não validou a conexão."
        elif "timeout expired" in detail or "connection timed out" in detail:
            message = "CONNECTION_TIMEOUT: banco não respondeu no prazo."
        elif "could not translate host name" in detail:
            message = "DNS_FAILED: não foi possível resolver o endereço do banco."
        elif error.sqlstate == "42501":
            message = "PERMISSION_DENIED: login sem a permissão necessária."
        else:
            message = "Database connection or operation failed (UNCLASSIFIED)."
        raise StorageError(message) from None
