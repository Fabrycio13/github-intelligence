"""Read-only GitHub API transport; credentials never enter diagnostics."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

SAFE_QUERY_KEYS = frozenset(
    {
        "page",
        "per_page",
        "since",
        "until",
        "state",
        "sort",
        "direction",
        "base",
        "head",
        "sha",
        "path",
        "author",
        "committer",
        "filter",
        "check_name",
        "status",
        "ref",
    }
)


def _safe_url(path: str, params: dict[str, Any] | None = None) -> httpx.URL:
    try:
        if not isinstance(path, str) or any(ord(c) < 33 for c in path) or "\\" in path:
            raise ValueError
        if path.startswith("/") and not path.startswith("//"):
            path = "https://api.github.com" + path
        parts = urlsplit(path)
        if parts.scheme != "https" or parts.netloc != "api.github.com" or parts.fragment:
            raise ValueError
        decoded = unquote(parts.path)
        if (
            not parts.path.startswith("/")
            or "%" in decoded
            or "\\" in decoded
            or any(p in {".", ".."} for p in decoded.split("/"))
            or decoded.count("/") != parts.path.count("/")
        ):
            raise ValueError
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        if any(k not in SAFE_QUERY_KEYS for k, _ in pairs):
            raise ValueError
        if params and any(k not in SAFE_QUERY_KEYS for k in params):
            raise ValueError
        url = httpx.URL(path)
        if params:
            url = url.copy_merge_params(params)
        return url
    except (ValueError, TypeError, httpx.InvalidURL):
        raise GitHubError("Unsafe GitHub API URL or query") from None


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"


class GitHubError(Exception):
    """A deliberately sanitized API failure."""

    def __init__(self, message: str = "GitHub API request failed", status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], object] = time.sleep,
        max_attempts: int = 3,
        max_wait: float = 60,
    ):
        if type(max_attempts) is not int or max_attempts < 1 or not math.isfinite(max_wait) or max_wait < 0:
            raise GitHubError("Invalid retry configuration")
        if token is not None and (not isinstance(token, str) or any(not 33 <= ord(c) <= 126 for c in token)):
            raise GitHubError("Invalid GitHub authentication token")
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._max_wait = max_wait
        self._authenticated = bool(token)
        self._client = httpx.Client(
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
            transport=transport,
            timeout=30,
            follow_redirects=False,
        )

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def _request(self, url: httpx.URL) -> httpx.Response:
        waited = 0.0
        for attempt in range(self._max_attempts):
            response: httpx.Response | None = None
            try:
                response = self._client.get(url)
            except httpx.TimeoutException:
                if attempt + 1 == self._max_attempts:
                    raise GitHubError("GitHub API request timed out") from None
            except (httpx.RequestError, ValueError):
                raise GitHubError("GitHub API connection failed") from None
            if response is not None:
                status = response.status_code
                if 200 <= status < 300:
                    return response
                rate_limited = self._is_rate_limited(response)
                if not (rate_limited or 500 <= status < 600) or attempt + 1 == self._max_attempts:
                    raise GitHubError("GitHub API request failed", status)
            delay = self._retry_delay(response, attempt)
            status_code = response.status_code if response is not None else None
            if delay > self._max_wait - waited:
                raise GitHubError("GitHub retry wait exceeds configured budget", status_code)
            self._sleep(delay)
            waited += delay
        raise GitHubError("GitHub API retry limit reached")

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False
        if response.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in response.headers:
            return True
        # A secondary limit may have no retry headers. Inspect only for classification;
        # never expose the upstream message or body to the caller.
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return False
        message = body.get("message") if isinstance(body, dict) else None
        return isinstance(message, str) and any(
            text in message.lower() for text in ("secondary rate limit", "api rate limit exceeded")
        )

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        if response is None:
            return float(2 ** min(attempt, 30))
        try:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
            elif (
                response.headers.get("X-RateLimit-Remaining") == "0"
                and "X-RateLimit-Reset" in response.headers
            ):
                delay = float(response.headers["X-RateLimit-Reset"]) - time.time()
            else:
                delay = float(2 ** min(attempt, 30))
                if GitHubClient._is_rate_limited(response):
                    delay *= 60
            if not math.isfinite(delay):
                raise ValueError
            return max(0.0, delay)
        except (ValueError, TypeError, OverflowError):
            raise GitHubError("GitHub supplied invalid retry timing", response.status_code) from None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        response = self._request(_safe_url(path, params))
        return self._json(response)

    def paginate(
        self, path: str, params: dict[str, Any] | None = None, key: str | None = None
    ) -> list[dict[str, Any]]:
        """Collect pages without following links outside the original endpoint."""
        url = _safe_url(path, params)
        endpoint = url.path
        visited: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        identities: set[tuple[str, str | int]] = set()
        result: list[dict[str, Any]] = []
        while True:
            signature = (url.path, tuple(sorted(url.params.multi_items())))
            if signature in visited:
                raise GitHubError("Repeated GitHub pagination link")
            visited.add(signature)
            response = self._request(url)
            body = self._json(response)
            if key is not None:
                rows = body.get(key) if isinstance(body, dict) else None
            else:
                rows = body
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise GitHubError("GitHub returned an invalid pagination JSON shape", response.status_code)
            for row in rows:
                identity = None
                for field in ("id", "sha"):
                    value = row.get(field)
                    if type(value) in (int, str) and value != "":
                        identity = (field, value)
                        break
                if identity is not None:
                    if identity in identities:
                        continue
                    identities.add(identity)
                result.append(row)
            next_link = response.links.get("next")
            if next_link is None:
                return result
            url = _safe_url(next_link.get("url", ""))
            if url.path != endpoint or len(url.params) != len(url.params.multi_items()):
                raise GitHubError("Unsafe GitHub pagination link")

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any] | list[Any]:
        try:
            value = response.json()
        except (ValueError, UnicodeError):
            raise GitHubError("GitHub returned invalid JSON", response.status_code) from None
        if not isinstance(value, (dict, list)):
            raise GitHubError("GitHub returned an invalid JSON shape", response.status_code)
        return value
