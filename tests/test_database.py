"""Public connection configuration and mocked driver security contract."""

import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest

from github_intelligence import database
from github_intelligence.config import ConfigurationError
from github_intelligence.database import DatabaseSettings
from github_intelligence.secrets import SecretError
from github_intelligence.storage import StorageError

REF = "cpkgvpuivjzplerzlpxz"
PASSWORD = "sentinel-secret-do-not-display"


def environment() -> dict[str, str]:
    return {
        "GHI_DATABASE_HOST": f"db.{REF}.supabase.co",
        "GHI_DATABASE_USER": "github_intelligence_worker",
        "GHI_DATABASE_PROJECT_REF": REF,
        "GHI_DATABASE_PASSWORD": PASSWORD,
    }


def test_environment_settings_are_frozen_and_password_is_hidden() -> None:
    settings = DatabaseSettings.from_environment(environment())
    assert settings.host == f"db.{REF}.supabase.co"
    assert settings.user == "github_intelligence_worker"
    assert settings.project_ref == REF
    assert settings.port == 5432
    assert settings.sslrootcert == "system"
    assert settings.password == PASSWORD
    assert PASSWORD not in repr(settings)
    with pytest.raises(FrozenInstanceError):
        settings.host = "localhost"  # type: ignore[misc]


@pytest.mark.parametrize(("field", "value"), [
    ("HOST", "localhost"), ("HOST", "127.0.0.1"), ("HOST", "/tmp/socket"),
    ("HOST", f"postgresql://db.{REF}.supabase.co"),
    ("HOST", f"db.{REF}.supabase.co host=evil"),
    ("HOST", "db.aaaaaaaaaaaaaaaaaaaa.supabase.co"),
    ("HOST", "aws-0-us-east-1.pooler.supabase.com.evil"),
    ("HOST", "aws-0-us-east-1.pooler.supabase.com:5432"),
    ("HOST", "aws-0-us-east-1.pooler.supabase.com\n"),
    ("USER", "postgres"), ("USER", "service_role"), ("USER", "github_intelligence_runtime"),
    ("USER", f"github_intelligence_worker.{REF}"), ("USER", " github_intelligence_worker"),
    ("PROJECT_REF", REF.upper()), ("PROJECT_REF", REF + "a"), ("PROJECT_REF", "a" * 19),
    ("PROJECT_REF", "1" * 20), ("PASSWORD", ""), ("PASSWORD", "bad\x00secret"),
    ("PASSWORD", "\ud800"), ("SSLROOTCERT", "disable"), ("SSLROOTCERT", ""),
])
def test_rejects_unsafe_settings_without_echoing_values(field: str, value: str) -> None:
    env = environment()
    env[f"GHI_DATABASE_{field}"] = value
    with pytest.raises(ConfigurationError) as error:
        DatabaseSettings.from_environment(env)
    assert PASSWORD not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("missing", ["HOST", "USER", "PROJECT_REF", "PASSWORD"])
def test_missing_settings_are_configuration_errors(missing: str) -> None:
    env = environment()
    del env[f"GHI_DATABASE_{missing}"]
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_environment(env)


def test_session_pooler_requires_project_qualified_worker() -> None:
    env = environment()
    env["GHI_DATABASE_HOST"] = "aws-0-us-east-1.pooler.supabase.com"
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_environment(env)
    env["GHI_DATABASE_USER"] += f".{REF}"
    assert DatabaseSettings.from_environment(env).user == f"github_intelligence_worker.{REF}"
    env["GHI_DATABASE_USER"] = "github_intelligence_worker.aaaaaaaaaaaaaaaaaaaa"
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_environment(env)


def test_constructor_cannot_bypass_validation() -> None:
    with pytest.raises(ConfigurationError):
        DatabaseSettings("localhost", "postgres", REF, PASSWORD)
    for port in (6543, True, "5432"):
        with pytest.raises(ConfigurationError):
            DatabaseSettings(f"db.{REF}.supabase.co", "github_intelligence_worker", REF,
                             PASSWORD, port=port)  # type: ignore[arg-type]


def test_uses_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment().items():
        monkeypatch.setenv(name, value)
    assert DatabaseSettings.from_environment().password == PASSWORD


@pytest.mark.parametrize("document", [
    "{", "[]", "null", '{"host":"a","host":"b"}',
    '{"password":"sentinel-secret-do-not-display"}', '{"dsn":"postgresql://secret"}',
    '{"unknown":true}', '{"port":true}', '{"port":"5432"}', '{"port":6543}',
    '{"host":1}', '{"user":null}', '{"project_ref":[]}', '{"password_path":1}',
    '{"sslrootcert":false}',
])
def test_strict_nonsecret_json(tmp_path: Path, document: str) -> None:
    path = tmp_path / "database.json"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        DatabaseSettings.from_environment(environment(), config_path=path)
    assert PASSWORD not in str(error.value)
    assert error.value.__cause__ is None


def test_explicit_missing_config_is_not_silently_ignored(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_environment(environment(), config_path=tmp_path / "missing.json")


def test_config_relative_secret_and_ca_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "database.json"
    ca = tmp_path / "ca.pem"
    ca.write_text("test CA", encoding="ascii")
    path.write_text(json.dumps({
        "host": f"db.{REF}.supabase.co", "user": "github_intelligence_worker", "project_ref": REF,
        "password_path": "password.dpapi", "sslrootcert": "ca.pem", "port": 5432,
    }), encoding="utf-8")
    reader = Mock(return_value=PASSWORD.encode())
    monkeypatch.setattr("github_intelligence.database.load_private_key", reader)
    settings = DatabaseSettings.from_environment({}, config_path=path)
    assert settings.password == PASSWORD
    assert settings.sslrootcert == str(ca.resolve())
    reader.assert_called_once_with(tmp_path / "password.dpapi")
    reader.reset_mock()
    settings = DatabaseSettings.from_environment(environment(), config_path=path)
    assert settings.password == PASSWORD
    reader.assert_not_called()


@pytest.mark.parametrize("suffix", [".txt", ".pem", ".dpapi.txt", ".DPAPI", ""])
def test_plaintext_password_paths_are_never_read(suffix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reader = Mock(return_value=PASSWORD.encode())
    monkeypatch.setattr("github_intelligence.database.load_private_key", reader)
    env = environment()
    del env["GHI_DATABASE_PASSWORD"]
    env["GHI_DATABASE_PASSWORD_PATH"] = "secret" + suffix
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_environment(env)
    reader.assert_not_called()


@pytest.mark.parametrize("result", [
    b"", b"\xff", b"bad\x00password", SecretError(PASSWORD), OSError(PASSWORD),
])
def test_dpapi_failures_are_sanitized(result: bytes | Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    reader = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr("github_intelligence.database.load_private_key", reader)
    env = environment()
    del env["GHI_DATABASE_PASSWORD"]
    env["GHI_DATABASE_PASSWORD_PATH"] = "secret.dpapi"
    with pytest.raises(ConfigurationError) as error:
        DatabaseSettings.from_environment(env)
    assert PASSWORD not in str(error.value)
    assert error.value.__cause__ is None


def test_environment_overrides_nonsecret_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "database.json"
    path.write_text(json.dumps({"host": "localhost", "sslrootcert": "missing.pem"}), encoding="utf-8")
    env = environment()
    env["GHI_DATABASE_SSLROOTCERT"] = "system"
    env.pop("GHI_DATABASE_PASSWORD")
    env["GHI_DATABASE_PASSWORD_PATH"] = str(tmp_path / "other.dpapi")
    reader = Mock(return_value=PASSWORD.encode())
    monkeypatch.setattr("github_intelligence.database.load_private_key", reader)
    assert DatabaseSettings.from_environment(env, config_path=path).password == PASSWORD
    reader.assert_called_once_with(tmp_path / "other.dpapi")


def test_connect_passes_only_explicit_secure_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Mock()
    driver = Mock(return_value=connection)
    monkeypatch.setattr(psycopg, "connect", driver)
    # Ambient insecure libpq defaults must not replace these explicit parameters.
    monkeypatch.setenv("PGSSLMODE", "disable")
    monkeypatch.setenv("PGOPTIONS", "-c role=postgres")
    settings = DatabaseSettings.from_environment(environment())
    with database.connect_database(settings) as opened:
        assert opened is connection
        connection.close.assert_not_called()
    driver.assert_called_once_with(
        host=settings.host, user=settings.user, password=PASSWORD, port=5432, dbname="postgres",
        sslmode="verify-full", sslrootcert="system", autocommit=True, prepare_threshold=None,
        connect_timeout=10, application_name="github-intelligence", options="", passfile=os.devnull,
    )
    connection.close.assert_called_once_with()


@pytest.mark.parametrize("stage", ["connect", "body", "close"])
def test_driver_errors_are_sanitized_and_connections_closed(
    stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Mock()
    error = psycopg.OperationalError(f"{PASSWORD} postgres://secret diagnostic SELECT secret")
    driver = Mock(side_effect=error) if stage == "connect" else Mock(return_value=connection)
    if stage == "close":
        connection.close.side_effect = error
    monkeypatch.setattr(psycopg, "connect", driver)
    with pytest.raises(StorageError) as caught:
        with database.connect_database(DatabaseSettings.from_environment(environment())):
            if stage == "body":
                raise error
    assert PASSWORD not in str(caught.value)
    assert "SELECT" not in str(caught.value)
    assert "postgres://" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    if stage != "connect":
        connection.close.assert_called_once_with()


def test_closes_on_non_driver_exception_without_replacing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Mock()
    monkeypatch.setattr(psycopg, "connect", Mock(return_value=connection))
    with pytest.raises(RuntimeError, match="application failure"):
        with database.connect_database(DatabaseSettings.from_environment(environment())):
            raise RuntimeError("application failure")
    connection.close.assert_called_once_with()


def test_explicit_ca_reaches_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("test CA", encoding="ascii")
    env = environment()
    env["GHI_DATABASE_SSLROOTCERT"] = str(ca)
    driver = Mock(return_value=Mock())
    monkeypatch.setattr(psycopg, "connect", driver)
    with database.connect_database(DatabaseSettings.from_environment(env)):
        pass
    assert driver.call_args.kwargs["sslrootcert"] == str(ca)
