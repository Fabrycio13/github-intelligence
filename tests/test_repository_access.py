"""Repository access checks use generated keys and a synthetic GitHub transport only."""

import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from github_intelligence.config import AppSettings
from github_intelligence.github import GitHubError


@pytest.fixture(scope="module")
def settings():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    return AppSettings(app_id="123", installation_id=999, private_key=pem)


class GitHubFixture:
    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.responses = {
            "/app": (200, {"id": 123, "slug": "repo-reader"}),
            "/repos/Octo/Repo/installation": (200, {"id": 456, "app_id": 123, "suspended_at": None}),
            "/app/installations/456/access_tokens": (
                201, {"token": "synthetic-token", "permissions": {
                    name: "read" for name in ("contents", "pull_requests", "checks", "statuses", "metadata")
                }},
            ),
            "/repos/Octo/Repo": (200, {"id": 789, "full_name": "octo/repo", "default_branch": "main"}),
        }

    def handle(self, request):
        self.calls.append(request)
        assert request.url.host == "api.github.com"
        assert request.url.scheme == "https"
        assert not request.url.query
        assert request.headers["X-GitHub-Api-Version"]
        if request.url.path == "/repos/Octo/Repo":
            assert request.headers["Authorization"] == "Bearer synthetic-token"
        else:
            key = serialization.load_pem_private_key(self.settings.private_key, None).public_key()
            claims = jwt.decode(
                request.headers["Authorization"].removeprefix("Bearer "), key,
                algorithms=["RS256"], issuer="123",
            )
            assert claims["exp"] > claims["iat"]
        if request.method == "POST":
            assert request.url.path == "/app/installations/456/access_tokens"
            payload = json.loads(request.content)
            assert payload == {"permissions": {
                name: "read" for name in ("contents", "pull_requests", "checks", "statuses", "metadata")
            }}
        else:
            assert request.method == "GET"
            assert not request.content
        status, body = self.responses[request.url.path]
        return httpx.Response(status, json=body)

    def inspector(self):
        from github_intelligence.repository_access import RepositoryAccess

        return RepositoryAccess(self.settings, transport=httpx.MockTransport(self.handle))


def test_metadata_only_access_requires_authorization_instead_of_ready(settings):
    fixture = GitHubFixture(settings)
    fixture.responses["/app/installations/456/access_tokens"] = (
        201, {"token": "synthetic-token", "permissions": {"metadata": "read"}},
    )
    result = fixture.inspector().inspect("Octo/Repo")
    assert result["status"] == "authorization_required"
    assert "permissões" in result["message"]
    assert not any(request.url.path == "/repos/Octo/Repo" for request in fixture.calls)


def test_ready_verifies_app_installation_and_repository_with_separate_credentials(settings):
    fixture = GitHubFixture(settings)
    result = fixture.inspector().inspect("https://github.com/Octo/Repo.git")
    assert result == {
        "status": "ready", "full_name": "octo/repo", "github_id": 789,
        "app_id": 123, "installation_id": 456, "main_branch": "main",
    }
    assert all(type(result[name]) is int for name in ("github_id", "app_id", "installation_id"))
    assert [request.url.path for request in fixture.calls] == list(fixture.responses)



@pytest.mark.parametrize("suffix", ["", "/", ".git", ".git/"])
def test_https_repository_links(suffix):
    from github_intelligence.repository_access import parse_repository_link

    assert parse_repository_link(f"https://github.com/Octo/Repo{suffix}") == "Octo/Repo"


@pytest.mark.parametrize("link", [
    " Octo/Repo", "Octo/Repo ", "Octo/Repo\n", "Octo/Repo\t", "Octo/Repo/",
    "http://github.com/Octo/Repo", "https://evil.test/Octo/Repo",
    "https://github.com.evil.test/Octo/Repo", "https://user@github.com/Octo/Repo",
    "https://github.com:443/Octo/Repo", "https://github.com/Octo/Repo?",
    "https://github.com/Octo/Repo#", "https://github.com/Octo/Repo?token=secret",
    "https://github.com/Octo/Repo/tree/main", "https://github.com/Octo/../Repo",
    "https://github.com/Octo/%52epo", "https://github.com/Octo/Repo\\\\evil",
    "https://github.com/Octo/Repo\n", "https://github.com/Octo/Repo\x00",
    "https://github.com//Octo/Repo", "Octo/.", "Octo/..",
    "https://github.com/Octo/.git", "Octo/%52epo", "Octo/Repo?", "Octo/Repo#",
    "", None, 123, "owner/repo/extra", "https://github.com/Octo/Repo//",
])
def test_unsafe_links_are_rejected(link):
    from github_intelligence.repository_access import parse_repository_link

    with pytest.raises(ValueError):
        parse_repository_link(link)


@pytest.mark.parametrize("body", [
    [], {}, {"id": True, "slug": "repo-reader"}, {"id": "123", "slug": "repo-reader"},
    {"id": 0, "slug": "repo-reader"}, {"id": 124, "slug": "repo-reader"},
    {"id": 123}, *({"id": 123, "slug": slug} for slug in (
        "", "../evil", "foo?token=secret", "foo/bar", "foo%2fbar", "foo\n", None, 123,
        "-foo", "foo-", "foo_bar", "föö",
    )),
])
def test_untrusted_app_identity_is_rejected_before_installation_lookup(settings, body):
    fixture = GitHubFixture(settings)
    fixture.responses["/app"] = (200, body)
    with pytest.raises(GitHubError) as exc:
        fixture.inspector().inspect("Octo/Repo")
    assert "secret" not in str(exc.value)
    assert len(fixture.calls) == 1


def test_installation_404_returns_safe_manual_authorization_and_can_be_rechecked(settings):
    fixture = GitHubFixture(settings)
    fixture.responses["/repos/Octo/Repo/installation"] = (404, {"message": "secret upstream"})
    inspector = fixture.inspector()
    result = inspector.inspect("Octo/Repo")
    assert set(result) == {"status", "full_name", "authorization_url", "message"}
    assert result["status"] == "authorization_required"
    assert result["full_name"] == "Octo/Repo"
    assert result["authorization_url"] == "https://github.com/apps/repo-reader/installations/new"
    assert isinstance(result["message"], str) and result["message"]
    assert "secret" not in str(result)
    assert "nonexistent" not in result["message"].lower()
    assert len(fixture.calls) == 2
    fixture.responses["/repos/Octo/Repo/installation"] = (
        200, {"id": 456, "app_id": 123, "suspended_at": None},
    )
    assert inspector.inspect("Octo/Repo")["status"] == "ready"


@pytest.mark.parametrize("body", [
    [], {}, {"id": 456, "app_id": 123},
    *({"id": value, "app_id": 123, "suspended_at": None} for value in (True, "456", 0, -1, None)),
    *({"id": 456, "app_id": value, "suspended_at": None} for value in (True, "123", 999, None)),
    *({"id": 456, "app_id": 123, "suspended_at": value} for value in ("2026-01-01", "", False, 0)),
])
def test_untrusted_or_suspended_installation_never_mints_token(settings, body):
    fixture = GitHubFixture(settings)
    fixture.responses["/repos/Octo/Repo/installation"] = (200, body)
    with pytest.raises(GitHubError):
        fixture.inspector().inspect("Octo/Repo")
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("body", [
    [], {},
    *({"id": value, "full_name": "Octo/Repo"} for value in (True, "789", 0, -1, None)),
    *({"id": 789, "full_name": value} for value in (
        "Octo/Renamed", "Other/Repo", "Octo/Repo/", " Octo/Repo", None, 123,
    )),
    {"id": 789, "full_name": "Octo/Repo", "default_branch": 123},
])
def test_repository_identity_must_be_strict_and_not_silently_follow_renames(settings, body):
    fixture = GitHubFixture(settings)
    fixture.responses["/repos/Octo/Repo"] = (200, body)
    with pytest.raises(GitHubError):
        fixture.inspector().inspect("Octo/Repo")


@pytest.mark.parametrize("branch", [None, "main", "release/2026"])
def test_main_branch_can_be_unavailable(settings, branch):
    fixture = GitHubFixture(settings)
    fixture.responses["/repos/Octo/Repo"][1]["default_branch"] = branch
    assert fixture.inspector().inspect("Octo/Repo")["main_branch"] == branch


@pytest.mark.parametrize("endpoint,status", [
    (endpoint, status)
    for endpoint in ("/app", "/repos/Octo/Repo/installation", "/repos/Octo/Repo")
    for status in (301, 302, 401, 403, 404, 429, 500)
    if not (endpoint.endswith("/installation") and status == 404)
])
def test_api_failures_other_than_installation_404_are_sanitized(settings, endpoint, status):
    fixture = GitHubFixture(settings)
    fixture.responses[endpoint] = (status, {"message": "secret-token-and-key"})
    with pytest.raises(GitHubError) as exc:
        fixture.inspector().inspect("Octo/Repo")
    assert exc.value.status_code == status
    assert "secret" not in str(exc.value)
    assert "synthetic-token" not in repr(exc.value)
    assert fixture.calls[-1].url.path == endpoint


@pytest.mark.parametrize("endpoint", [
    "/app", "/repos/Octo/Repo/installation", "/app/installations/456/access_tokens", "/repos/Octo/Repo",
])
@pytest.mark.parametrize("failure", ["network", "json"])
def test_transport_and_json_errors_are_redacted_at_each_stage(settings, endpoint, failure):
    fixture = GitHubFixture(settings)

    def handle(request):
        if request.url.path == endpoint:
            if failure == "network":
                raise httpx.ReadTimeout("secret-token-and-key", request=request)
            return httpx.Response(200 if request.method == "GET" else 201, text="secret-token-and-key")
        return fixture.handle(request)

    from github_intelligence.repository_access import RepositoryAccess

    with pytest.raises(GitHubError) as exc:
        RepositoryAccess(settings, transport=httpx.MockTransport(handle)).inspect("Octo/Repo")
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__


def test_unsafe_token_permissions_stop_before_repository_read(settings):
    fixture = GitHubFixture(settings)
    fixture.responses["/app/installations/456/access_tokens"] = (
        201, {"token": "synthetic-token", "permissions": {"contents": "write"}},
    )
    with pytest.raises(GitHubError):
        fixture.inspector().inspect("Octo/Repo")
    assert len(fixture.calls) == 3


def test_invalid_input_never_reaches_transport(settings):
    fixture = GitHubFixture(settings)
    with pytest.raises(ValueError) as exc:
        fixture.inspector().inspect("https://github.com/Octo/Repo?token=secret")
    assert not fixture.calls
    assert "secret" not in str(exc.value)


def test_literal_repository_identifier_is_preserved():
    from github_intelligence.repository_access import parse_repository_link

    assert parse_repository_link("Octo/Repo.git") == "Octo/Repo.git"
