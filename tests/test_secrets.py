import sys

import pytest

from github_intelligence import secrets


def test_plain_test_bytes_are_loaded(tmp_path):
    path = tmp_path / "test.pem"
    path.write_bytes(b"generated-test-bytes\x00not-a-real-key")
    assert secrets.load_private_key(path) == b"generated-test-bytes\x00not-a-real-key"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI integration")
def test_current_user_dpapi_roundtrip(tmp_path):
    generated = b"GENERATED TEST STRING\x00\xffnot-a-real-private-key"
    path = tmp_path / "app.dpapi"
    secrets.protect_private_key(generated, path)
    encrypted = path.read_bytes()
    assert encrypted.startswith(b"GHI-DPAPI-v1\0")
    assert generated not in encrypted
    assert secrets.load_private_key(path) == generated
    renamed = tmp_path / "marker.pem"
    renamed.write_bytes(encrypted)
    assert secrets.load_private_key(renamed) == generated


def test_protect_rejects_non_bytes_without_echo(tmp_path):
    with pytest.raises(secrets.SecretError) as error:
        secrets.protect_private_key("sensitive-test-value", tmp_path / "key.dpapi")
    assert "sensitive-test-value" not in str(error.value)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI integration")
def test_tampered_ciphertext_rejected(tmp_path):
    path = tmp_path / "test.dpapi"
    secrets.protect_private_key(b"generated test", path)
    encrypted = bytearray(path.read_bytes())
    encrypted[-1] ^= 0x80
    path.write_bytes(encrypted)
    with pytest.raises(secrets.SecretError):
        secrets.load_private_key(path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows publication semantics")
def test_existing_file_never_overwritten_and_temporary_file_removed(tmp_path):
    path = tmp_path / "test.dpapi"
    path.write_bytes(b"existing")
    with pytest.raises(secrets.SecretError):
        secrets.protect_private_key(b"generated test", path)
    assert path.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [path]


def test_wrong_os_fails_without_creating_file(tmp_path, monkeypatch):
    path = tmp_path / "test.dpapi"
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    with pytest.raises(secrets.SecretError, match="Windows"):
        secrets.protect_private_key(b"generated test", path)
    path.write_bytes(b"GHI-DPAPI-v1\0generated test")
    with pytest.raises(secrets.SecretError, match="Windows"):
        secrets.load_private_key(path)


@pytest.mark.parametrize("data", [b"", b"unmarked-test", b"GHI-DPAPI-v1\0"])
def test_invalid_dpapi_file_rejected(tmp_path, data):
    path = tmp_path / "invalid.DPAPI"
    path.write_bytes(data)
    with pytest.raises(secrets.SecretError):
        secrets.load_private_key(path)


def test_cleanup_failure_remains_sanitized(tmp_path, monkeypatch):
    def fail_unlink(*args, **kwargs):
        raise OSError("sensitive-test-value")

    (tmp_path / "key.dpapi").write_bytes(b"existing")
    monkeypatch.setattr(type(tmp_path), "unlink", fail_unlink)
    with pytest.raises(secrets.SecretError) as error:
        secrets.protect_private_key(b"generated test", tmp_path / "key.dpapi")
    assert "sensitive-test-value" not in str(error.value)


def test_missing_and_empty_plain_file_rejected(tmp_path):
    path = tmp_path / "test.pem"
    with pytest.raises(secrets.SecretError):
        secrets.load_private_key(path)
    path.write_bytes(b"")
    with pytest.raises(secrets.SecretError):
        secrets.load_private_key(path)
