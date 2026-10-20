import httpx
import pytest

from github_intelligence.github import GitHubClient, GitHubError


@pytest.mark.parametrize("token", ["secret\n", "secret\r\nheader", "sëcret", "secret\x00"])
def test_invalid_header_tokens_are_safely_rejected(token):
    with pytest.raises(GitHubError) as exc:
        GitHubClient(token)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("message", ["You have exceeded a secondary rate limit.", "API rate limit exceeded"])
def test_rate_limited_403_body_without_headers_retries(message):
    calls = []
    sleeps: list[float] = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(403, json={"message": message})
        return httpx.Response(200, json=[])

    with GitHubClient(transport=httpx.MockTransport(handle), sleep=sleeps.append) as client:
        assert client.get("/repos/o/r") == []
    assert len(calls) == 2
    assert sleeps == [60]


def test_pagination_key_requires_object_response():
    with GitHubClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))) as client:
        with pytest.raises(GitHubError, match="shape"):
            client.paginate("/repos/o/r/check-runs", key="check_runs")


def test_context_manager_closes_transport():
    class Transport(httpx.BaseTransport):
        closed = False

        def handle_request(self, request):
            return httpx.Response(200, json=[])

        def close(self):
            self.closed = True

    transport = Transport()
    with GitHubClient(transport=transport) as client:
        assert client.get("/repos/o/r") == []
    assert transport.closed


def test_connection_error_is_sanitized_and_not_retried():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ConnectError("secret", request=request)

    with GitHubClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GitHubError) as exc:
            client.get("/repos/o/r")
    assert "secret" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("key", [None, "check_runs"])
def test_pagination_deduplicates_stable_identity_preserving_unidentified_rows(key):
    calls = []
    pages = [
        [{"id": 1}, {"sha": "abc"}, {"name": "anonymous"}],
        [{"id": 1}, {"sha": "abc"}, {"id": 2}, {"name": "anonymous"}],
    ]

    def handle(request):
        calls.append(request)
        index = len(calls) - 1
        body = pages[index] if key is None else {key: pages[index], "total_count": 7}
        headers = (
            {"Link": '<https://api.github.com/repos/o/r/check-runs?page=2>; rel="next"'} if index == 0 else {}
        )
        return httpx.Response(200, json=body, headers=headers)

    with GitHubClient(transport=httpx.MockTransport(handle)) as client:
        result = client.paginate("/repos/o/r/check-runs", key=key)
    assert result == [{"id": 1}, {"sha": "abc"}, {"name": "anonymous"}, {"id": 2}, {"name": "anonymous"}]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.test/repos/o/r/pulls?page=2",
        "//evil.test/repos/o/r/pulls?page=2",
        "https://user:secret@api.github.com/repos/o/r/pulls?page=2",
        "https://api.github.com/repos/other/private/pulls?page=2",
        "https://api.github.com/repos/o/r/pulls?access_token=secret",
        "https://api.github.com/repos/o/r/pulls?page=2#secret",
        "https://api.github.com/repos/o/r/pulls?unknown=secret",
        "https://api.github.com/repos/o/r/pulls?page=2&page=3",
    ],
)
def test_pagination_rejects_ssrf_and_endpoint_or_unsafe_query_changes(next_url):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=[{"id": 1}], headers={"Link": f'<{next_url}>; rel="next"'})

    with GitHubClient("secret", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GitHubError) as exc:
            client.paginate("/repos/o/r/pulls")
    assert len(calls) == 1
    assert "secret" not in str(exc.value)


def test_pagination_detects_repeated_link_independent_of_query_order():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=[],
            headers={"Link": '<https://api.github.com/repos/o/r/pulls?per_page=100&page=1>; rel="next"'},
        )

    with GitHubClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GitHubError, match="[Rr]epeated"):
            client.paginate("/repos/o/r/pulls", {"page": 1, "per_page": 100})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body,key", [({}, None), ({}, "check_runs"), ({"check_runs": {}}, "check_runs"), ([1], None)]
)
def test_pagination_does_not_swallow_invalid_json_shapes(body, key):
    with GitHubClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        with pytest.raises(GitHubError, match="shape"):
            client.paginate("/repos/o/r/pulls", key=key)


@pytest.mark.parametrize(
    "status,headers,expected",
    [
        (429, {"Retry-After": "2"}, 2),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "103"}, 3),
        (403, {"Retry-After": "1"}, 1),
        (500, {}, 1),
        (503, {}, 1),
        (429, {"Retry-After": "Thu, 01 Jan 1970 00:01:44 GMT"}, 4),
    ],
)
def test_transient_errors_retry_with_bounded_wait(monkeypatch, status, headers, expected):
    monkeypatch.setattr("github_intelligence.github.time.time", lambda: 100)
    calls = []
    sleeps: list[float] = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers=headers) if len(calls) == 1 else httpx.Response(200, json=[])

    with GitHubClient(transport=httpx.MockTransport(handle), sleep=sleeps.append) as client:
        assert client.get("/repos/o/r") == []
    assert len(calls) == 2
    assert sleeps == [expected]


def test_timeout_retry_exhaustion_is_sanitized():
    calls = []
    sleeps: list[float] = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret token and url", request=request)

    with GitHubClient("secret", transport=httpx.MockTransport(handle), sleep=sleeps.append) as client:
        with pytest.raises(GitHubError) as exc:
            client.get("/repos/o/r")
    assert len(calls) == 3
    assert sleeps == [1, 2]
    assert exc.value.status_code is None
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__


@pytest.mark.parametrize(
    "headers",
    [
        {"Retry-After": "99999"},
        {"X-RateLimit-Reset": "99999", "X-RateLimit-Remaining": "0"},
        {"Retry-After": "NaN"},
        {"Retry-After": "infinity"},
        {"Retry-After": "garbage"},
    ],
)
def test_invalid_or_excessive_server_wait_fails_without_sleep(monkeypatch, headers):
    monkeypatch.setattr("github_intelligence.github.time.time", lambda: 100)
    sleeps: list[float] = []
    with GitHubClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers=headers)),
        sleep=sleeps.append,
        max_wait=5,
    ) as client:
        with pytest.raises(GitHubError):
            client.get("/repos/o/r")
    assert sleeps == []


def test_retry_wait_budget_is_total_not_per_attempt():
    sleeps: list[float] = []
    with GitHubClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        sleep=sleeps.append,
        max_attempts=5,
        max_wait=2,
    ) as client:
        with pytest.raises(GitHubError) as exc:
            client.get("/repos/o/r")
    assert sleeps == [1]
    assert exc.value.status_code == 503


@pytest.mark.parametrize(
    "kwargs", [{"max_attempts": 0}, {"max_attempts": True}, {"max_wait": -1}, {"max_wait": float("inf")}]
)
def test_invalid_retry_configuration_rejected(kwargs):
    with pytest.raises(GitHubError):
        GitHubClient(**kwargs)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.test/repos/o/r",
        "http://api.github.com/repos/o/r",
        "https://user:password@api.github.com/repos/o/r",
        "//evil.test/a",
        "/repos/o/r#secret",
        "/repos/o/r?access_token=secret",
        "/a/../b",
        "/a%2fb",
        "https://api.github.com:444/repos/o/r",
        "/repos/o/r\\bad",
    ],
)
def test_unsafe_urls_are_rejected_before_transport(path):
    def handle(request):
        pytest.fail("unsafe request reached transport")

    with GitHubClient("secret", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GitHubError) as exc:
            client.get(path)
        assert "secret" not in str(exc.value)


def test_absolute_github_url_and_public_mode():
    def handle(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json=[])

    with GitHubClient(transport=httpx.MockTransport(handle)) as client:
        assert not client.authenticated
        assert client.get("https://api.github.com/repos/o/r", {"per_page": 100}) == []


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 422])
def test_errors_are_sanitized_and_never_redirect_or_retry(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="secret body", headers={"Location": "https://evil.test"})

    with GitHubClient("secret", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GitHubError) as exc:
            client.get("/repos/o/r")
    assert exc.value.status_code == status
    assert "secret" not in str(exc.value)
    assert "secret" not in repr(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"not-json secret", b"42", b"null", b'"secret"'])
def test_invalid_json_and_shapes_raise_sanitized_errors(body):
    with GitHubClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as client:
        with pytest.raises(GitHubError, match="JSON") as exc:
            client.get("/repos/o/r")
    assert "secret" not in str(exc.value)


def test_get_is_authenticated_read_only_and_versioned():
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.github.com/repos/o/r"
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        return httpx.Response(200, json={"id": 1})

    with GitHubClient("secret", transport=httpx.MockTransport(handle)) as client:
        assert client.authenticated
        assert "secret" not in repr(client)
        assert client.get("/repos/o/r") == {"id": 1}
