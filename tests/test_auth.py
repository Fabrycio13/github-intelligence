import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from github_intelligence.auth import create_app_jwt, installation_token
from github_intelligence.github import GitHubError


@pytest.mark.parametrize("app_id", ["", " ", "123\n", "secret/id", None])
def test_invalid_app_id_is_safely_rejected(private_key, app_id):
    with pytest.raises(GitHubError):
        create_app_jwt(app_id, private_key)


def test_naive_jwt_time_is_rejected_not_interpreted_in_local_timezone(private_key):
    with pytest.raises(GitHubError):
        create_app_jwt("123", private_key, now=datetime(2025, 1, 1))


@pytest.mark.parametrize("token", ["", "secret\x00", "secret\n", "sëcret"])
def test_exchange_rejects_non_header_safe_tokens(private_key, token):
    body = {"token": token, "permissions": READ_PERMISSIONS}
    with pytest.raises(GitHubError):
        installation_token(
            "123", private_key, 456, transport=httpx.MockTransport(lambda r: httpx.Response(201, json=body))
        )


def test_exchange_bad_json_is_redacted(private_key):
    with pytest.raises(GitHubError, match="JSON") as exc:
        installation_token(
            "123",
            private_key,
            456,
            transport=httpx.MockTransport(lambda r: httpx.Response(201, text="secret")),
        )
    assert "secret" not in str(exc.value)


READ_PERMISSIONS = {name: "read" for name in ("contents", "pull_requests", "checks", "statuses", "metadata")}


def test_installation_exchange_is_fixed_endpoint_scoped_post(private_key):
    def handle(request):
        assert str(request.url) == "https://api.github.com/app/installations/456/access_tokens"
        assert request.method == "POST"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        public_key = (
            serialization.load_pem_private_key(private_key, None)
            .public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        )
        assert jwt.decode(token, public_key, algorithms=["RS256"])["iss"] == "123"
        assert json.loads(request.content) == {"repository_ids": [7, 8], "permissions": READ_PERMISSIONS}
        assert request.extensions["timeout"]["read"] == 30
        return httpx.Response(
            201,
            json={
                "token": "installation-secret",
                "permissions": READ_PERMISSIONS,
                "repositories": [{"id": 7}, {"id": 8}],
            },
        )

    assert (
        installation_token(
            "123", private_key, 456, repository_ids=[7, 8], transport=httpx.MockTransport(handle)
        )
        == "installation-secret"
    )


def test_installation_exchange_without_repository_scope(private_key):
    def handle(request):
        assert json.loads(request.content) == {"permissions": READ_PERMISSIONS}
        return httpx.Response(201, json={"token": "secret", "permissions": READ_PERMISSIONS})

    assert installation_token("123", private_key, 456, transport=httpx.MockTransport(handle)) == "secret"


@pytest.mark.parametrize("repository_ids", [[], [0], [-1], [True], ["7"], [7, 7]])
def test_invalid_repository_scope_never_reaches_network(private_key, repository_ids):
    def handle(request):
        pytest.fail("invalid scope reached transport")

    with pytest.raises(GitHubError):
        installation_token(
            "123", private_key, 456, repository_ids=repository_ids, transport=httpx.MockTransport(handle)
        )


@pytest.mark.parametrize("installation_id", [0, -1, True, "456", "456/../../evil"])
def test_invalid_installation_id_never_reaches_network(private_key, installation_id):
    def handle(request):
        pytest.fail("invalid installation id reached transport")

    with pytest.raises(GitHubError):
        installation_token("123", private_key, installation_id, transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"token": "secret"},
        {"token": "secret", "permissions": {}},
        {"token": "secret", "permissions": {"contents": "write"}},
        {"token": "secret", "permissions": {"administration": "read"}},
        {"token": "secret", "permissions": {"deployments": "write"}},
        {"token": "secret", "permissions": ["read"]},
        {"token": 42, "permissions": READ_PERMISSIONS},
    ],
)
def test_installation_response_rejects_bad_token_or_unsafe_permissions(private_key, body):
    with pytest.raises(GitHubError) as exc:
        installation_token(
            "123", private_key, 456, transport=httpx.MockTransport(lambda r: httpx.Response(201, json=body))
        )
    assert "secret" not in str(exc.value)
    assert "secret" not in repr(exc.value)


@pytest.mark.parametrize(
    "repositories", [None, [], [{"id": 7}, {"id": 999}], [{"id": 7}], [{"id": "7"}], {"id": 7}]
)
def test_installation_response_verifies_requested_repository_scope(private_key, repositories):
    body = {"token": "secret", "permissions": READ_PERMISSIONS, "repositories": repositories}
    with pytest.raises(GitHubError):
        installation_token(
            "123",
            private_key,
            456,
            repository_ids=[7, 8],
            transport=httpx.MockTransport(lambda r: httpx.Response(201, json=body)),
        )


@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
def test_exchange_failures_are_redacted_and_redirects_not_followed(private_key, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="secret-token-and-key", headers={"Location": "https://evil.test"})

    with pytest.raises(GitHubError) as exc:
        installation_token("123", private_key, 456, transport=httpx.MockTransport(handle))
    assert exc.value.status_code == status
    assert "secret" not in str(exc.value)
    assert len(calls) == 1


def test_exchange_transport_failure_is_redacted(private_key):
    def handle(request):
        raise httpx.ReadTimeout("secret-key-and-token", request=request)

    with pytest.raises(GitHubError) as exc:
        installation_token("123", private_key, 456, transport=httpx.MockTransport(handle))
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.fixture(scope="module")
def private_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def test_app_jwt_signature_claims_and_expiry(private_key):
    now = datetime.now(UTC)
    token = create_app_jwt("123", private_key, now=now)
    public_key = (
        serialization.load_pem_private_key(private_key, None)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    payload = jwt.decode(token, public_key, algorithms=["RS256"], issuer="123")
    assert jwt.get_unverified_header(token)["alg"] == "RS256"
    assert payload["iat"] == int(now.timestamp()) - 60
    assert payload["exp"] == int(now.timestamp()) + 9 * 60
    expired = create_app_jwt("123", private_key, now=now - timedelta(hours=1))
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired, public_key, algorithms=["RS256"])


def test_bad_private_key_errors_are_redacted():
    with pytest.raises(GitHubError) as exc:
        create_app_jwt("123", b"private secret key")
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__
