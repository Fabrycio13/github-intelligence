import json
import sys

import pytest

from github_intelligence.config import AppSettings, ConfigurationError
from github_intelligence.secrets import protect_private_key


def test_app_secrets_are_not_in_repr_and_not_accepted_in_chat_config(tmp_path):
    key = tmp_path / "test-key.pem"
    key.write_text("test-only-key", encoding="utf-8")
    settings = AppSettings.from_environment(
        {"GITHUB_APP_ID": "123", "GITHUB_INSTALLATION_ID": "456", "GITHUB_PRIVATE_KEY_PATH": str(key)}
    )
    assert settings.private_key == b"test-only-key"
    assert "test-only-key" not in repr(settings)


def test_missing_credentials_does_not_silently_switch_to_public():
    with pytest.raises(ConfigurationError, match="GitHub App"):
        AppSettings.from_environment({})


def test_bad_installation_or_key_has_sanitized_error():
    for values in [
        {"GITHUB_APP_ID": "123", "GITHUB_INSTALLATION_ID": "secret", "GITHUB_PRIVATE_KEY_PATH": "x"},
        {
            "GITHUB_APP_ID": "123",
            "GITHUB_INSTALLATION_ID": "456",
            "GITHUB_PRIVATE_KEY_PATH": "missing-secret",
        },
    ]:
        with pytest.raises(ConfigurationError) as error:
            AppSettings.from_environment(values)
        assert "secret" not in str(error.value)


def test_project_config_loads_relative_key_and_env_overrides_individual_fields(tmp_path):
    (tmp_path / "test.pem").write_bytes(b"generated test")
    path = tmp_path / "app.local.json"
    path.write_text(json.dumps({
        "app_id": "5380709", "installation_id": 123, "private_key_path": "test.pem",
    }), encoding="utf-8")
    settings = AppSettings.from_environment({}, config_path=path)
    assert settings.app_id == "5380709"
    assert settings.installation_id == 123
    assert settings.private_key == b"generated test"
    assert "generated test" not in repr(settings)
    override = AppSettings.from_environment({"GITHUB_INSTALLATION_ID": "456"}, config_path=path)
    assert override.installation_id == 456


@pytest.mark.parametrize("changes", [
    {"app_id": True}, {"app_id": 5380709}, {"app_id": "-1"},
    {"installation_id": True}, {"installation_id": "123"}, {"installation_id": 1.5},
    {"installation_id": 0}, {"private_key_path": []}, {"private_key_path": ""},
    {"private_key": "sensitive-test"},
])
def test_config_schema_is_strict_and_errors_are_sanitized(tmp_path, changes):
    path = tmp_path / "app.local.json"
    (tmp_path / "test.pem").write_bytes(b"generated test")
    data = {"app_id": "5380709", "installation_id": 123, "private_key_path": "test.pem"}
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        AppSettings.from_environment({}, config_path=path)
    assert "sensitive-test" not in str(error.value)


@pytest.mark.parametrize("raw", [
    "[]", "null", '"sensitive-test"', '{"sensitive-test":',
    '{"app_id":"123","app_id":"456"}',
    '{"app_id":"123","app_id":"456","installation_id":123,"private_key_path":"test.pem"}',
])
def test_malformed_config_is_sanitized(tmp_path, raw):
    (tmp_path / "test.pem").write_bytes(b"generated test")
    path = tmp_path / "app.local.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        AppSettings.from_environment({}, config_path=path)
    assert "sensitive-test" not in str(error.value)


def test_explicit_mapping_ignores_process_environment_and_missing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    with pytest.raises(ConfigurationError):
        AppSettings.from_environment({}, config_path=tmp_path / "missing.json")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI integration")
def test_config_reads_dpapi_and_wraps_decryption_failure(tmp_path):
    key = tmp_path / "test.dpapi"
    protect_private_key(b"generated non-key bytes", key)
    config = tmp_path / "app.local.json"
    config.write_text(json.dumps({
        "app_id": "5380709", "installation_id": 123, "private_key_path": str(key),
    }), encoding="utf-8")
    settings = AppSettings.from_environment({}, config_path=config)
    assert settings.private_key == b"generated non-key bytes"
    assert "generated non-key bytes" not in repr(settings)
    key.write_bytes(b"GHI-DPAPI-v1\0invalid-test")
    with pytest.raises(ConfigurationError):
        AppSettings.from_environment({}, config_path=config)


def test_environment_key_and_ids_override_config_and_default_uses_environment(tmp_path, monkeypatch):
    key = tmp_path / "override.pem"
    key.write_bytes(b"generated override")
    config = tmp_path / "app.local.json"
    config.write_text(json.dumps({
        "app_id": "5380709", "installation_id": 123, "private_key_path": "missing.pem",
    }), encoding="utf-8")
    env = {"GITHUB_APP_ID": "999", "GITHUB_INSTALLATION_ID": "456", "GITHUB_PRIVATE_KEY_PATH": str(key)}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for settings in (
        AppSettings.from_environment(env, config_path=config),
        AppSettings.from_environment(config_path=tmp_path / "missing.json"),
    ):
        assert settings.app_id == "999"
        assert settings.installation_id == 456
        assert settings.private_key == b"generated override"
