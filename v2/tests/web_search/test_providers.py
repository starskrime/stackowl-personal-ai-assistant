"""Unit tests for the three concrete web-search providers (SearXNG / Brave / DDG).

NO real network. httpx is mocked by monkeypatching ``httpx.AsyncClient.get`` to return a
real ``httpx.Response`` (so ``raise_for_status`` / ``.json()`` run exactly as in prod);
``ddgs`` is mocked by monkeypatching the ``_run_search`` worker. Egress logging is
asserted to contain host+path only — never the query string or the API key.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from stackowl.web_search.providers.brave import BraveProvider
from stackowl.web_search.providers.ddg import DdgProvider
from stackowl.web_search.providers.searxng import SearxngProvider

# --------------------------------------------------------------------------- fixtures

_SEARXNG_FIXTURE: dict[str, Any] = {
    "results": [
        {"title": "First", "url": "https://a.example/1", "content": "desc one", "score": 9.0},
        {"title": "Second", "url": "https://a.example/2", "content": "desc two", "score": 8.0},
        {"title": "Third", "url": "https://a.example/3", "content": "desc three", "score": 7.0},
    ]
}

_BRAVE_FIXTURE: dict[str, Any] = {
    "web": {
        "results": [
            {"title": "B-One", "url": "https://b.example/1", "description": "b desc one"},
            {"title": "B-Two", "url": "https://b.example/2", "description": "b desc two"},
            {"title": "B-Three", "url": "https://b.example/3", "description": "b desc three"},
        ]
    }
}

_DDG_FIXTURE: list[dict[str, Any]] = [
    {"title": "D-One", "href": "https://d.example/1", "body": "d body one"},
    {"title": "D-Two", "href": "https://d.example/2", "body": "d body two"},
    {"title": "D-Three", "href": "https://d.example/3", "body": "d body three"},
]


def _mock_get(
    monkeypatch: pytest.MonkeyPatch,
    *,
    json_body: Any = None,
    status_code: int = 200,
    raise_exc: Exception | None = None,
) -> dict[str, Any]:
    """Patch ``httpx.AsyncClient.stream`` to capture the call and drive the read path.

    The providers stream the body (Fix A — bounded read) instead of ``client.get`` +
    ``resp.json()``, so this helper canned-responds on ``.stream``. It still returns the
    same capture dict (``url`` / ``params`` / ``headers``) the existing tests inspect, and
    serialises ``json_body`` to the streamed bytes so ``json.loads`` runs exactly as in
    prod. ``raise_exc`` is raised on entry to simulate a transport error.
    """
    captured: dict[str, Any] = {}
    body = b"" if json_body is None else json.dumps(json_body).encode("utf-8")

    class _StreamCtx:
        async def __aenter__(self) -> _StreamCtx:
            if raise_exc is not None:
                raise raise_exc
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            if status_code >= 400 or 300 <= status_code < 400:
                request = httpx.Request("GET", captured.get("url", "http://x/"))
                response = httpx.Response(status_code, request=request)
                raise httpx.HTTPStatusError(
                    f"HTTP {status_code}", request=request, response=response
                )

        async def aiter_bytes(self) -> Any:
            yield body

    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> _StreamCtx:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        return _StreamCtx()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    return captured


def _mock_stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes = b"{}",
    status_code: int = 200,
) -> dict[str, Any]:
    """Patch ``httpx.AsyncClient`` construction + ``.stream`` for the streaming read path.

    Captures the client constructor kwargs (so a test can assert ``follow_redirects``) and
    returns a canned streaming response whose ``raise_for_status`` honours ``status_code``
    and whose ``aiter_bytes`` yields ``body`` in one chunk.
    """
    captured: dict[str, Any] = {}

    real_init = httpx.AsyncClient.__init__

    def fake_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        captured["follow_redirects"] = kwargs.get("follow_redirects")
        captured["timeout"] = kwargs.get("timeout")
        real_init(self, *args, **kwargs)

    class _StreamCtx:
        async def __aenter__(self) -> _StreamCtx:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            if status_code >= 400 or 300 <= status_code < 400:
                request = httpx.Request("GET", "http://x/")
                response = httpx.Response(status_code, request=request)
                raise httpx.HTTPStatusError(
                    f"HTTP {status_code}", request=request, response=response
                )

        async def aiter_bytes(self) -> Any:
            yield body

    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> _StreamCtx:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        return _StreamCtx()

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)
    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    return captured


# --------------------------------------------------------------------------- SearXNG


def test_searxng_unavailable_when_base_url_empty() -> None:
    assert SearxngProvider("").is_available() is False
    assert SearxngProvider("   ").is_available() is False


def test_searxng_available_when_base_url_set() -> None:
    assert SearxngProvider("http://localhost:8080").is_available() is True


def test_searxng_name_and_capabilities() -> None:
    p = SearxngProvider("http://localhost:8080")
    assert p.name == "searxng"
    assert p.supports_search() is True
    assert p.supports_extract() is False


async def test_searxng_parses_fixture_and_caps_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_get(monkeypatch, json_body=_SEARXNG_FIXTURE)
    provider = SearxngProvider("http://localhost:8080/")  # trailing slash normalised
    result = await provider.search("kittens", limit=2)

    assert result.success is True
    assert len(result.web) == 2  # capped at limit
    first = result.web[0]
    assert first.title == "First"
    assert first.url == "https://a.example/1"
    assert first.description == "desc one"  # SearXNG `content` → description
    assert first.position == 1
    assert result.web[1].position == 2


async def test_searxng_5xx_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_get(monkeypatch, json_body={}, status_code=503)
    result = await SearxngProvider("http://localhost:8080").search("q", limit=5)
    assert result.success is False
    assert result.error is not None
    assert "503" in result.error
    assert result.web == ()


async def test_searxng_timeout_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_get(monkeypatch, raise_exc=httpx.ConnectTimeout("slow"))
    result = await SearxngProvider("http://localhost:8080").search("q", limit=5)
    assert result.success is False
    assert result.error is not None


async def test_searxng_empty_results_is_success_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty results[] → success with empty web[]; the registry decides cascade.
    _mock_get(monkeypatch, json_body={"results": []})
    result = await SearxngProvider("http://localhost:8080").search("q", limit=5)
    assert result.success is True
    assert result.web == ()


async def test_searxng_oversized_body_returns_failure_no_oom(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix A: a body exceeding _MAX_RESPONSE_BYTES must be rejected mid-stream (OOM guard),
    # never buffered whole. We mock client.stream to yield oversized chunks lazily so the
    # ceiling trips before the full (unbounded) body would be read.
    from stackowl.web_search.providers import searxng as searxng_mod

    chunk = b"x" * (1024 * 1024)  # 1 MiB per chunk
    n_chunks = (searxng_mod._MAX_RESPONSE_BYTES // len(chunk)) + 2  # guaranteed to exceed
    yielded = 0

    class _FakeStreamCtx:
        async def __aenter__(self) -> _FakeStreamCtx:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self) -> Any:
            nonlocal yielded
            for _ in range(n_chunks):
                yielded += 1
                yield chunk

    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> _FakeStreamCtx:
        return _FakeStreamCtx()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    result = await SearxngProvider("http://localhost:8080").search("q", limit=5)

    assert result.success is False
    assert result.error is not None
    assert "too large" in result.error
    # The guard tripped before consuming the whole (theoretically unbounded) stream.
    assert yielded < n_chunks


async def test_searxng_302_redirect_not_followed_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix B: a 302 surfaces (follow_redirects=False) → raise_for_status rejects → failure.
    captured: dict[str, Any] = _mock_stream(monkeypatch, status_code=302, body=b"")
    result = await SearxngProvider("http://localhost:8080").search("q", limit=5)
    assert result.success is False
    assert result.error is not None
    # Client was constructed with redirect-following explicitly disabled.
    assert captured["follow_redirects"] is False


# --------------------------------------------------------------------------- Brave


def test_brave_unavailable_when_no_key_ref() -> None:
    assert BraveProvider(None).is_available() is False
    assert BraveProvider("").is_available() is False


def test_brave_unavailable_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAVE_TEST_KEY", raising=False)
    assert BraveProvider("BRAVE_TEST_KEY").is_available() is False


def test_brave_available_when_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAVE_TEST_KEY", "secret-token-xyz")
    assert BraveProvider("BRAVE_TEST_KEY").is_available() is True


def test_brave_name_and_capabilities() -> None:
    p = BraveProvider("BRAVE_TEST_KEY")
    assert p.name == "brave"
    assert p.supports_search() is True
    assert p.supports_extract() is False


async def test_brave_parses_fixture_and_caps_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAVE_TEST_KEY", "secret-token-xyz")
    captured = _mock_get(monkeypatch, json_body=_BRAVE_FIXTURE)
    result = await BraveProvider("BRAVE_TEST_KEY").search("foxes", limit=2)

    assert result.success is True
    assert len(result.web) == 2
    first = result.web[0]
    assert first.title == "B-One"
    assert first.url == "https://b.example/1"
    assert first.description == "b desc one"
    assert first.position == 1
    # Key travels in the header, not the URL.
    assert captured["headers"]["X-Subscription-Token"] == "secret-token-xyz"


async def test_brave_missing_key_never_calls_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAVE_TEST_KEY", raising=False)

    async def boom(self: httpx.AsyncClient, *a: Any, **k: Any) -> httpx.Response:
        raise AssertionError("network must not be hit when key is missing")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    result = await BraveProvider("BRAVE_TEST_KEY").search("q", limit=5)
    assert result.success is False
    assert result.error is not None


async def test_brave_5xx_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAVE_TEST_KEY", "secret-token-xyz")
    _mock_get(monkeypatch, json_body={}, status_code=500)
    result = await BraveProvider("BRAVE_TEST_KEY").search("q", limit=5)
    assert result.success is False
    assert result.error is not None
    assert "500" in result.error


async def test_brave_oversized_body_returns_failure_no_oom(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix A (brave): oversized streamed body rejected mid-stream → failure, no OOM.
    from stackowl.web_search.providers import brave as brave_mod

    monkeypatch.setenv("BRAVE_TEST_KEY", "secret-token-xyz")
    chunk = b"x" * (1024 * 1024)
    n_chunks = (brave_mod._MAX_RESPONSE_BYTES // len(chunk)) + 2
    yielded = 0

    class _FakeStreamCtx:
        async def __aenter__(self) -> _FakeStreamCtx:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self) -> Any:
            nonlocal yielded
            for _ in range(n_chunks):
                yielded += 1
                yield chunk

    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> _FakeStreamCtx:
        return _FakeStreamCtx()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    result = await BraveProvider("BRAVE_TEST_KEY").search("q", limit=5)

    assert result.success is False
    assert result.error is not None
    assert "too large" in result.error
    assert yielded < n_chunks


async def test_brave_302_redirect_not_followed_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix B (brave): 302 surfaces (follow_redirects=False) → failure, redirect not chased.
    monkeypatch.setenv("BRAVE_TEST_KEY", "secret-token-xyz")
    captured = _mock_stream(monkeypatch, status_code=302, body=b"")
    result = await BraveProvider("BRAVE_TEST_KEY").search("q", limit=5)
    assert result.success is False
    assert result.error is not None
    assert captured["follow_redirects"] is False


async def test_brave_never_logs_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BRAVE_TEST_KEY", "super-secret-value-123")
    _mock_get(monkeypatch, json_body=_BRAVE_FIXTURE)
    with caplog.at_level(logging.DEBUG, logger="stackowl.tool"):
        await BraveProvider("BRAVE_TEST_KEY").search("foxes secret query", limit=2)

    blob = "\n".join(r.getMessage() + str(getattr(r, "_fields", "")) for r in caplog.records)
    assert "super-secret-value-123" not in blob


# --------------------------------------------------------------------------- DDG


def test_ddg_available_when_module_importable() -> None:
    # ddgs is a declared dependency, so importable in the test env.
    assert DdgProvider().is_available() is True


def test_ddg_name_and_capabilities() -> None:
    p = DdgProvider()
    assert p.name == "ddg"
    assert p.supports_search() is True
    assert p.supports_extract() is False


async def test_ddg_parses_fixture_and_caps_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DdgProvider()
    monkeypatch.setattr(provider, "_run_search", lambda q, limit: list(_DDG_FIXTURE))
    result = await provider.search("badgers", limit=2)

    assert result.success is True
    assert len(result.web) == 2
    first = result.web[0]
    assert first.title == "D-One"
    assert first.url == "https://d.example/1"  # href → url
    assert first.description == "d body one"  # body → description
    assert first.position == 1
    assert result.web[1].position == 2


async def test_ddg_raises_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DdgProvider()

    def boom(q: str, limit: int) -> list[dict[str, Any]]:
        raise RuntimeError("ddgs blew up")

    monkeypatch.setattr(provider, "_run_search", boom)
    result = await provider.search("q", limit=5)
    assert result.success is False
    assert result.error is not None
    assert "ddg search failed" in result.error
    assert result.web == ()


async def test_ddg_unavailable_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DdgProvider()
    monkeypatch.setattr(provider, "is_available", lambda: False)
    result = await provider.search("q", limit=5)
    assert result.success is False
    assert result.error is not None


# --------------------------------------------------------------------- egress logging


async def test_searxng_egress_log_is_host_and_path_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _mock_get(monkeypatch, json_body=_SEARXNG_FIXTURE)
    with caplog.at_level(logging.DEBUG, logger="stackowl.tool"):
        await SearxngProvider("http://localhost:8080").search("a very secret query string", limit=2)

    egress_records = [r for r in caplog.records if "egress" in getattr(r, "_fields", {})]
    assert egress_records, "expected an egress log record"
    egress = egress_records[0]._fields["egress"]
    assert egress == "http://localhost:8080/search"
    # No query string / query text leaked into the egress field.
    assert "a very secret query string" not in egress
    assert "?" not in egress
    assert "q=" not in egress


async def test_brave_egress_log_has_no_key_or_query(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BRAVE_TEST_KEY", "key-should-not-appear")
    _mock_get(monkeypatch, json_body=_BRAVE_FIXTURE)
    with caplog.at_level(logging.DEBUG, logger="stackowl.tool"):
        await BraveProvider("BRAVE_TEST_KEY").search("private brave query", limit=2)

    egress_records = [r for r in caplog.records if "egress" in getattr(r, "_fields", {})]
    assert egress_records, "expected an egress log record"
    egress = egress_records[0]._fields["egress"]
    assert egress == "https://api.search.brave.com/res/v1/web/search"
    assert "key-should-not-appear" not in egress
    assert "private brave query" not in egress
    assert "?" not in egress
