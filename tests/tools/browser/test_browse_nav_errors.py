"""Navigation-error classification for the inner browser loop (self-heal W5.T19).

ORIGINAL PRODUCTION BUG: ``page.goto`` on a bad/typo URL raised a raw Playwright
``Error: Page.goto: NS_ERROR_UNKNOWN_HOST`` that propagated UNHANDLED out of
``execute``, was logged by ``tools/base.py`` as a scary "unhandled exception —
wrapping" ERROR, and fed the inner agent a raw Playwright traceback string.

These tests drive the REAL ``BrowserBrowseTool.execute`` loop (same faked-services
seam as ``test_browse_no_progress_integration.py``) and assert that a goto failure
is CLASSIFIED structurally by stable Playwright error CODES (not localized prose)
and surfaced as a clean failed-but-handled ToolResult — never re-raised.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

import stackowl.tools.browser.browse as browse_mod
from stackowl.config.browser import BrowserSettings
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.tools.browser.browse import BrowserBrowseTool

_MAX_STEPS = 5


class _GotoFailPage:
    """Fake page whose ``goto`` always raises the configured exception."""

    url = "https://broken.test/start"

    def __init__(self, goto_exc: BaseException) -> None:
        self._goto_exc = goto_exc
        self.goto_calls = 0

    async def goto(self, _url: str, **_kwargs: object) -> None:
        self.goto_calls += 1
        raise self._goto_exc

    async def title(self) -> str:
        return "Broken"

    async def content(self) -> str:
        return "<html><body>x</body></html>"


class _FakeSessions:
    def __init__(self, page: _GotoFailPage) -> None:
        self._page = page
        self.open_calls = 0
        self.close_calls = 0

    async def open(self, _owner_key: str) -> str:
        self.open_calls += 1
        return "sess-fixed"

    async def get_page(
        self, _session_id: str, _handle: str | None = None
    ) -> tuple[object, _GotoFailPage, str]:
        return (object(), self._page, "handle-fixed")

    async def close(self, _session_id: str) -> None:
        self.close_calls += 1


class _RuntimeStub:
    """Carries ``.settings`` plus the domain-slot/record-navigation seam.

    ``acquire_domain_slot`` records each call so a regression test can assert the
    rate-limit slot is acquired+released cleanly even on the goto-failure path
    (the leaky-bucket releases its lock internally before returning — there is no
    held resource to leak — so the guard simply confirms no hang/leak occurs).
    """

    def __init__(self, settings: BrowserSettings) -> None:
        self.settings = settings
        self.acquire_calls: list[str] = []
        self.record_calls = 0

    async def acquire_domain_slot(self, url: str) -> None:
        self.acquire_calls.append(url)

    async def record_navigation(self) -> None:
        self.record_calls += 1


class _NoActionProvider:
    """Inner provider that never gets consulted (goto fails before the LLM call)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, _messages: list[Message], model: str, **_kwargs: object
    ) -> CompletionResult:
        self.calls += 1
        action_json = json.dumps({"action": "done", "summary": "x"})
        return CompletionResult(
            content=f"```json\n{action_json}\n```",
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name="fake",
            duration_ms=0.0,
        )


class _FakeProviderRegistry:
    def __init__(self, provider: _NoActionProvider) -> None:
        self._provider = provider

    def get_by_tier(self, _tier: str) -> tuple[_NoActionProvider, str]:
        return self._provider, ""


@pytest.fixture
def _patched_perception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_captcha(_page: Any) -> None:
        return None

    async def _const_elements(_page: Any) -> list[dict[str, Any]]:
        return [{"index": 0, "tag": "div", "text": "x"}]

    def _const_markdown(_html: str, *, include_links: bool = False) -> str:
        return "constant page text"

    monkeypatch.setattr(browse_mod, "detect_captcha", _no_captcha)
    monkeypatch.setattr(browse_mod, "index_dom_elements", _const_elements)
    monkeypatch.setattr(browse_mod, "extract_markdown", _const_markdown)


async def _run_with_seed_goto_exc(
    goto_exc: BaseException,
) -> tuple[Any, _GotoFailPage, _RuntimeStub, _FakeSessions]:
    """Drive execute() with a seed_url whose goto raises ``goto_exc``."""
    provider = _NoActionProvider()
    page = _GotoFailPage(goto_exc)
    sessions = _FakeSessions(page)
    runtime = _RuntimeStub(BrowserSettings())
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        browser_runtime=runtime,  # type: ignore[arg-type]
        browser_sessions=sessions,  # type: ignore[arg-type]
        audit_logger=None,
    )
    token = set_services(services)
    try:
        result = await BrowserBrowseTool().execute(
            task="visit a broken url",
            allowed_domains=[],
            max_steps=_MAX_STEPS,
            seed_url="https://broken.test/start",
        )
    finally:
        reset_services(token)
    return result, page, runtime, sessions


async def test_unknown_host_classified_not_unhandled(_patched_perception: None) -> None:
    """NS_ERROR_UNKNOWN_HOST → handled, classified 'unknown_host', NOT re-raised."""
    exc = PlaywrightError("Page.goto: NS_ERROR_UNKNOWN_HOST")
    # execute() must NOT propagate the Playwright error.
    result, page, _runtime, sessions = await _run_with_seed_goto_exc(exc)

    assert page.goto_calls == 1  # the goto WAS attempted

    payload = json.loads(result.output)
    # Failed-but-handled: a clean ToolResult, not a raised exception.
    assert result.success is False
    # Stable code-based classification — NOT the raw Playwright traceback.
    blob = json.dumps(payload) + (result.error or "")
    assert "unknown_host" in blob, payload
    assert "NS_ERROR_UNKNOWN_HOST" not in (result.error or ""), result.error
    # New session opened + cleaned up.
    assert sessions.close_calls == 1


async def test_timeout_classified(_patched_perception: None) -> None:
    """A Playwright TimeoutError → classified 'timeout' (by error TYPE, not prose)."""
    exc = PlaywrightTimeout("Page.goto: Timeout 30000ms exceeded")
    result, _page, _runtime, _sessions = await _run_with_seed_goto_exc(exc)

    payload = json.loads(result.output)
    assert result.success is False
    blob = json.dumps(payload) + (result.error or "")
    assert "timeout" in blob, payload


async def test_connection_reset_classified(_patched_perception: None) -> None:
    """A connection-reset code → handled generic-ish nav failure, not unhandled."""
    exc = PlaywrightError("Page.goto: NS_ERROR_CONNECTION_REFUSED")
    result, _page, _runtime, _sessions = await _run_with_seed_goto_exc(exc)

    payload = json.loads(result.output)
    assert result.success is False
    blob = json.dumps(payload) + (result.error or "")
    # Classified as a connection-reset (stable code) — not unhandled.
    assert "connection_reset" in blob or "navigation_failed" in blob, payload


async def test_generic_nav_failure_classified(_patched_perception: None) -> None:
    """An unrecognized Playwright nav error → 'navigation_failed', still handled."""
    exc = PlaywrightError("Page.goto: NS_ERROR_SOMETHING_WEIRD")
    result, _page, _runtime, _sessions = await _run_with_seed_goto_exc(exc)

    payload = json.loads(result.output)
    assert result.success is False
    blob = json.dumps(payload) + (result.error or "")
    assert "navigation_failed" in blob, payload


async def test_domain_slot_acquired_and_not_leaked_on_failure(
    _patched_perception: None,
) -> None:
    """REGRESSION (6-min hang): the domain slot is acquired then the failed nav
    returns cleanly — the leaky-bucket lock is released internally, so a failed
    goto cannot hang subsequent navigations. We assert the slot was acquired and
    the call returned (no hang) with a clean handled result.
    """
    exc = PlaywrightError("Page.goto: NS_ERROR_UNKNOWN_HOST")
    result, _page, runtime, _sessions = await _run_with_seed_goto_exc(exc)

    # Slot acquired for the seed nav.
    assert len(runtime.acquire_calls) == 1
    # The call returned (no hang) with a handled status — proves the goto failure
    # did not propagate and did not deadlock the loop.
    payload = json.loads(result.output)
    assert payload["status"] != "running", payload
    assert result.success is False
