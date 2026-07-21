"""Model-threading regression for the inner browse loop (per-model provider config).

``BrowserBrowseTool.execute`` resolves the inner-LLM provider ONCE before the
per-step loop via ``provider_registry.get_by_tier_and_model(tier)`` (Task 5),
which returns the concrete model string configured for that tier/provider —
not just the provider object. Every ``inner_provider.complete(...)`` call
inside the ``for step_idx in range(max_steps)`` loop must be passed that
SAME resolved model string, on every iteration, not only the first.

Wiring mirrors ``test_browse_no_progress_integration.py``'s faked-services
harness so the REAL loop runs (not just the top-level resolution call).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import stackowl.tools.browser.browse as browse_mod
from stackowl.config.browser import BrowserSettings
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.tools.browser.browse import BrowserBrowseTool

_MAX_STEPS = 10
_RESOLVED_MODEL = "vendor/inner-browse-model-v7"


class _StablePage:
    """Fake Playwright page whose observable state never changes across steps."""

    url = "https://example.test/stuck"

    async def title(self) -> str:
        return "Stuck Page"

    async def content(self) -> str:
        return "<html><body>unchanging</body></html>"

    async def evaluate(self, _script: str, *args: object) -> None:
        return None


class _FakeSessions:
    def __init__(self, page: _StablePage) -> None:
        self._page = page

    async def open(self, _owner_key: str) -> str:
        return "sess-fixed"

    async def get_page(
        self, _session_id: str, _handle: str | None = None
    ) -> tuple[object, _StablePage, str]:
        return (object(), self._page, "handle-fixed")

    async def close(self, _session_id: str) -> None:
        return None


class _RuntimeStub:
    def __init__(self, settings: BrowserSettings) -> None:
        self.settings = settings


class _ScriptedModelCapturingProvider:
    """Inner provider that records the ``model=`` kwarg passed on EVERY call.

    Replies with a scripted sequence of distinct actions (so the no-progress
    guard never trips before the script is exhausted) ending in ``done``.
    """

    def __init__(self, actions: list[dict[str, object]]) -> None:
        self._actions = actions
        self.calls = 0
        self.seen_models: list[str] = []

    async def complete(
        self, _messages: list[Message], model: str, **_kwargs: object
    ) -> CompletionResult:
        self.seen_models.append(model)
        action = self._actions[min(self.calls, len(self._actions) - 1)]
        self.calls += 1
        action_json = json.dumps(action)
        return CompletionResult(
            content=f"```json\n{action_json}\n```",
            input_tokens=1,
            output_tokens=1,
            model="fake-model",
            provider_name="fake",
            duration_ms=0.0,
        )


class _FakeProviderRegistry:
    """Registry-shaped fake matching the ``get_by_tier_and_model`` contract
    (Task 5): returns ``(provider, model)``, not just the provider.
    """

    def __init__(self, provider: _ScriptedModelCapturingProvider, model: str) -> None:
        self._provider = provider
        self._model = model
        self.tiers_requested: list[str] = []

    def get_by_tier_and_model(
        self, tier: str
    ) -> tuple[_ScriptedModelCapturingProvider, str]:
        self.tiers_requested.append(tier)
        return self._provider, self._model


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


async def test_resolved_model_reaches_every_step_of_the_loop(
    _patched_perception: None,
) -> None:
    """The SAME resolved model string must reach EVERY loop iteration's
    ``.complete()`` call — not just the first. Scripted as scroll-down,
    scroll-up, scroll-down, done: 4 distinct-enough actions so the
    no-progress guard never breaks the loop early, proving the model is
    threaded across multiple genuine iterations.
    """
    actions: list[dict[str, object]] = [
        {"action": "scroll", "direction": "down", "amount": "page"},
        {"action": "scroll", "direction": "up", "amount": "page"},
        {"action": "scroll", "direction": "down", "amount": "page"},
        {"action": "done", "summary": "finished"},
    ]
    provider = _ScriptedModelCapturingProvider(actions)
    registry = _FakeProviderRegistry(provider, _RESOLVED_MODEL)
    sessions = _FakeSessions(_StablePage())
    services = StepServices(
        provider_registry=registry,  # type: ignore[arg-type]
        browser_runtime=_RuntimeStub(BrowserSettings()),  # type: ignore[arg-type]
        browser_sessions=sessions,  # type: ignore[arg-type]
        audit_logger=None,
    )
    token = set_services(services)
    try:
        result = await BrowserBrowseTool().execute(
            task="scroll around then finish",
            allowed_domains=[],
            max_steps=_MAX_STEPS,
        )
    finally:
        reset_services(token)

    payload = json.loads(result.output)
    assert payload["status"] == "complete", payload
    assert result.success is True

    # The loop ran across all 4 scripted steps (proves multi-iteration coverage,
    # not just the first call).
    assert provider.calls == 4, provider.calls
    # EVERY call — across every loop iteration — carried the SAME resolved
    # model string threaded from get_by_tier_and_model(), never the old
    # hardcoded model="".
    assert provider.seen_models == [_RESOLVED_MODEL] * 4, provider.seen_models

    # The tier requested from the registry matches settings.inner_browse_model_tier.
    assert registry.tiers_requested == [BrowserSettings().inner_browse_model_tier]
