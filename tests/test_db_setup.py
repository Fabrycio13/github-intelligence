"""One-time local setup: save credentials only after a real health gate."""
from contextlib import contextmanager
from unittest.mock import Mock

import psycopg
import pytest
from test_database import environment

from github_intelligence import database
from github_intelligence.config import ConfigurationError
from github_intelligence.storage import StorageError


def test_auth_failure_is_actionable_without_exposing_driver_text(monkeypatch):
    error = psycopg.errors.InvalidPassword("SYNTHETIC_SECRET SELECT private")
    monkeypatch.setattr(psycopg, "connect", Mock(side_effect=error))
    with pytest.raises(StorageError, match="AUTHENTICATION_REJECTED") as caught:
        with database.connect_database(database.DatabaseSettings.from_environment(environment())):
            pass
    assert "SYNTHETIC_SECRET" not in str(caught.value)
    assert "SELECT" not in str(caught.value)


def test_setup_saves_verified_credentials_and_reuses_them(tmp_path, monkeypatch):
    from github_intelligence import db_setup
    from github_intelligence.secrets import load_private_key

    @contextmanager
    def connection(settings):
        assert settings.password == "synthetic-only"
        yield object()

    monkeypatch.setattr(db_setup, "connect_database", connection)
    monkeypatch.setattr(db_setup, "check_database", lambda conn: None)
    config = tmp_path / "database.json"
    secret = tmp_path / "password.dpapi"
    settings = database.DatabaseSettings.from_environment(environment())
    db_setup.save_verified_connection(
        settings.__class__(settings.host, settings.user, settings.project_ref, "synthetic-only"),
        config,
        secret,
    )
    assert "synthetic-only" not in config.read_text()
    assert load_private_key(secret) == b"synthetic-only"
    assert database.DatabaseSettings.from_environment({}, config_path=config).password == "synthetic-only"


def test_failed_setup_leaves_no_config_or_secret(tmp_path, monkeypatch):
    from github_intelligence import db_setup

    @contextmanager
    def failed(settings):
        raise StorageError("AUTHENTICATION_REJECTED")
        yield

    monkeypatch.setattr(db_setup, "connect_database", failed)
    config = tmp_path / "database.json"
    secret = tmp_path / "password.dpapi"
    with pytest.raises(StorageError):
        db_setup.save_verified_connection(
            database.DatabaseSettings.from_environment(environment()), config, secret,
        )
    assert not config.exists()
    assert not secret.exists()


def test_setup_refuses_to_overwrite_config_before_connecting(tmp_path, monkeypatch):
    from github_intelligence import db_setup

    config = tmp_path / "database.json"
    config.write_text("existing")
    connector = Mock()
    monkeypatch.setattr(db_setup, "connect_database", connector)
    with pytest.raises(ConfigurationError):
        db_setup.save_verified_connection(
            database.DatabaseSettings.from_environment(environment()), config, tmp_path / "password.dpapi",
        )
    connector.assert_not_called()
    assert config.read_text() == "existing"


def test_main_routes_pooler_user(tmp_path, monkeypatch):
    import sys

    from github_intelligence import db_setup

    saved = []
    @contextmanager
    def fake_connect(settings):
        yield object()

    monkeypatch.setattr(db_setup, "connect_database", fake_connect)
    monkeypatch.setattr(db_setup, "check_database", lambda conn: None)
    monkeypatch.setattr(db_setup, "save_verified_connection", lambda s, c, sec: saved.append(s))
    monkeypatch.setattr(db_setup.DatabaseSettings, "from_environment", lambda env, config_path=None: saved[0])
    monkeypatch.setenv("GHI_SETUP_PASSWORD", "test-pass")
    monkeypatch.setattr(sys, "argv", [
        "db_setup.py",
        "--host", "aws-0-sa-east-1.pooler.supabase.com",
        "--project-ref", "cpkgvpuivjzplerzlpxz",
        "--sslrootcert", "system",
    ])
    monkeypatch.setattr(db_setup.Path, "exists", lambda self: False)

    code = db_setup.main()
    assert code == 0
    assert len(saved) == 1
    assert saved[0].user == "github_intelligence_worker.cpkgvpuivjzplerzlpxz"
