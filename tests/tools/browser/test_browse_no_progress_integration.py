"""Integration test for the no-progress guard, driven through the REAL inner loop.

Unlike ``test_browse_no_progress.py`` (which exercises only the streak math in
isolation), this test drives ``BrowserBrowseTool.execute`` end-to-end with faked
services so the genuine ``for step_idx in range(max_steps)`` loop runs. An inner
agent that keeps emitting the SAME no-op action on an UNCHANGED page must trip
the guard and break with ``status="no_progress"`` WELL BEFORE ``max_steps``.

REGRESSION GUARD: the test counts ``inner_provider.complete`` calls and asserts
the loop stopped after ~``_NO_PROGRESS_LIMIT + 1`` steps. If the no-progress
break in ``browse.py`` were removed, the loop would run all ``max_steps``
iterations and these assertions would fail.

Wiring strategy
---------------
- Inject a fake ``StepServices`` via ``set_services`` (the same seam the other
  service-backed tool tests use, e.g. ``tests/tools/search/test_web_search.py``).
- ``browser_sessions`` is a minimal fake exposing ``open`` / ``get_page`` /
  ``close``. ``get_page`` always returns the SAME stable fake page whose
  ``.url`` / ``.title()`` / ``.content()`` are constant across steps, so the
  rendered ``state_text`` is byte-identical every iteration.
- ``browser_runtime`` is a stub carrying only ``.settings`` (the only attribute
  ``execute`` touches on the no-seed-url, no-navigate path).
- ``provider_registry.get_by_tier`` returns a counting fake provider whose
  ``.complete`` always returns the SAME ``{"action":"scroll",...}`` JSON — not
  ``done`` and a no-op against the faked page — so the (state_text, action)
  signature is identical on every step.
- ``index_dom_elements`` / ``extract_markdown`` / ``detect_captcha`` (module-level
  in ``browse.py``) are monkeypatched to constant values (no captcha).
- ``audit_logger`` is None (BatchAuditLogger no-ops the commit).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import stackowl.tools.browser.browse as browse_mod
from stackowl.config.browser import BrowserSettings
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.tools.browser.browse import _NO_PROGRESS_LIMIT, BrowserBrowseTool

_MAX_STEPS = 20


class _StablePage:
    """Fake Playwright page whose observable state never changes across steps."""

    url = "https://example.test/stuck"

    async def title(self) -> str:
        return "Stuck Page"

    async def content(self) -> str:
        return "<html><body>unchanging</body></html>"

    async def evaluate(self, _script: str, *args: object) -> None:
        # scroll handler calls page.evaluate — make it a no-op (no state change).
        return None


class _FakeSessions:
    """Minimal BrowserSessionRegistry stand-in returning one stable page."""

    def __init__(self, page: _StablePage) -> None:
        self._page = page
        self.open_calls = 0
        self.close_calls = 0

    async def open(self, _owner_key: str) -> str:
        self.open_calls += 1
        return "sess-fixed"

    async def get_page(
        self, _session_id: str, _handle: str | None = None
    ) -> tuple[object, _StablePage, str]:
        # Always the SAME page + handle → state_text is identical every step.
        return (object(), self._page, "handle-fixed")

    async def close(self, _session_id: str) -> None:
        self.close_calls += 1


class _RuntimeStub:
    """Carries only ``.settings`` — the sole attribute execute() reads on this path."""

    def __init__(self, settings: BrowserSettings) -> None:
        self.settings = settings


class _CountingProvider:
    """Inner provider whose complete() always returns the same no-op action JSON."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, _messages: list[Message], model: str, **_kwargs: object
    ) -> CompletionResult:
        self.calls += 1
        # scroll = NOT done, and a no-op against the faked page → identical sig.
        action_json = json.dumps({"action": "scroll", "direction": "down", "amount": "page"})
        return CompletionResult(
            content=f"```json\n{action_json}\n```",
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name="fake",
            duration_ms=0.0,
        )


class _FakeProviderRegistry:
    def __init__(self, provider: _CountingProvider) -> None:
        self._provider = provider
        self.tiers_requested: list[str] = []

    def get_by_tier(self, tier: str) -> tuple[_CountingProvider, str]:
        self.tiers_requested.append(tier)
        return self._provider, ""


@pytest.fixture
def _patched_perception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the module-level perception helpers to constant, captcha-free values."""

    async def _no_captcha(_page: Any) -> None:
        return None

    async def _const_elements(_page: Any) -> list[dict[str, Any]]:
        return [{"index": 0, "tag": "div", "text": "x"}]

    def _const_markdown(_html: str, *, include_links: bool = False) -> str:
        return "constant page text"

    monkeypatch.setattr(browse_mod, "detect_captcha", _no_captcha)
    monkeypatch.setattr(browse_mod, "index_dom_elements", _const_elements)
    monkeypatch.setattr(browse_mod, "extract_markdown", _const_markdown)


async def test_no_progress_guard_fires_through_real_loop(
    _patched_perception: None,
) -> None:
    """Same page + same action every step → loop breaks 'no_progress' early."""
    provider = _CountingProvider()
    sessions = _FakeSessions(_StablePage())
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        browser_runtime=_RuntimeStub(BrowserSettings()),  # type: ignore[arg-type]
        browser_sessions=sessions,  # type: ignore[arg-type]
        audit_logger=None,
    )
    token = set_services(services)
    try:
        result = await BrowserBrowseTool().execute(
            task="do something that never makes progress",
            allowed_domains=[],
            max_steps=_MAX_STEPS,
        )
    finally:
        reset_services(token)

    payload = json.loads(result.output)

    # 1. The guard tripped — status is the dedicated no_progress sentinel.
    assert payload["status"] == "no_progress", payload

    # 2. no_progress is a CLEAN stop, not an error → ToolResult.success is True.
    assert result.success is True

    # 3. The loop stopped EARLY. The streak counts repeats AFTER the first
    #    occurrence, so it trips on the (_NO_PROGRESS_LIMIT + 1)-th identical
    #    step. complete() is called once per step up to and including the break.
    #    This is the REGRESSION GUARD: with the break removed, the loop would run
    #    all _MAX_STEPS (=20) iterations and this assertion would fail.
    assert provider.calls == _NO_PROGRESS_LIMIT + 1, (
        f"expected the loop to break after {_NO_PROGRESS_LIMIT + 1} steps, "
        f"but inner provider was called {provider.calls} times "
        f"(max_steps={_MAX_STEPS})"
    )
    assert provider.calls < _MAX_STEPS  # proves it did NOT run to max_steps

    # 4. Opened exactly one session and closed it on exit (new-session cleanup).
    assert sessions.open_calls == 1
    assert sessions.close_calls == 1
