"""T6 (F120) — Gemini capability gate: an agentic turn never silently no-ops.

A provider whose ``supports_tools is False`` (currently GeminiProvider, which
inherits the base ``complete_with_tools`` that ignores ``tool_schemas`` and returns
``(content, [])``) must NOT be returned for an agentic (non-conversational) turn:

* with a tool-capable backup → the selector ROUTES AWAY to it (loud log);
* with NO tool-capable provider → it FLOORS HONESTLY (ToolUseUnsupportedError),
  never a silent tool-free reply;
* a conversational turn is UNAFFECTED (Gemini's stream/complete path is fine —
  gate the tool loop, not the provider);
* defense-in-depth: the base ``complete_with_tools`` reached with a non-empty
  ``tool_schemas`` raises ``ToolUseUnsupportedError`` instead of returning ``(c, [])``.

Drives the REAL ``select_tool_provider`` gate + a REAL ProviderRegistry; mocks
only the provider classes (canned, no network).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest

from stackowl.exceptions import ToolUseUnsupportedError
from stackowl.pipeline.provider_select import select_tool_provider
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


class _NoToolsProvider(ModelProvider):
    """A Gemini-like provider: complete/stream only, supports_tools is False."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "gemini"

    @property
    def supports_tools(self) -> bool:
        return False

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="tool-free reply", input_tokens=1, output_tokens=1,
            model="m", provider_name=self._name, duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        yield "tool-free reply"


class _ToolCapableProvider(ModelProvider):
    """An openai-like provider that DOES support the tool loop."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content="ok", input_tokens=1, output_tokens=1,
            model="m", provider_name=self._name, duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        yield "ok"


def _state(intent: str = "standard") -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="do a task",
        channel="cli", owl_name="owl-x", pipeline_step="execute",
        interactive=True, intent_class=intent,  # type: ignore[arg-type]
    )


def _services(reg: ProviderRegistry) -> StepServices:
    return StepServices(provider_registry=reg, owl_registry=None, tool_registry=ToolRegistry())


async def test_agentic_turn_routes_away_to_tool_capable_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Gemini (no-tools) tier provider on an agentic turn → routed to the backup."""
    reg = ProviderRegistry()
    reg.register_mock("gemini-powerful", _NoToolsProvider("gemini-powerful"), tier="powerful")
    reg.register_mock("openai-standard", _ToolCapableProvider("openai-standard"), tier="standard")

    with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
        chosen = select_tool_provider(reg, _services(reg), _state("standard"))

    # OUTCOME: a tool-capable provider was chosen — NOT the silent no-tools Gemini.
    assert chosen.name == "openai-standard"
    assert chosen.supports_tools is True
    # The route-away was logged LOUDLY.
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "cannot call tools" in blob


async def test_agentic_turn_floors_honestly_when_no_tool_capable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No tool-capable provider anywhere on an agentic turn → floor honestly (raise)."""
    reg = ProviderRegistry()
    reg.register_mock("gemini-powerful", _NoToolsProvider("gemini-powerful"), tier="powerful")
    reg.register_mock("gemini-fast", _NoToolsProvider("gemini-fast"), tier="fast")

    with (
        caplog.at_level(logging.ERROR, logger="stackowl.engine"),
        pytest.raises(ToolUseUnsupportedError),
    ):
        select_tool_provider(reg, _services(reg), _state("standard"))

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "no tool-capable provider" in blob


async def test_conversational_turn_keeps_gemini(
) -> None:
    """A conversational turn is NOT gated — Gemini's stream path stays available."""
    reg = ProviderRegistry()
    reg.register_mock("gemini-powerful", _NoToolsProvider("gemini-powerful"), tier="powerful")

    chosen = select_tool_provider(reg, _services(reg), _state("conversational"))
    # The conversational path keeps the Gemini provider (gate the loop, not the provider).
    assert chosen.name == "gemini-powerful"


async def test_base_complete_with_tools_raises_on_nonempty_schemas() -> None:
    """Defense-in-depth: the base default refuses to silently return (content, [])."""

    async def _dispatch(_n: str, _a: dict[str, Any]) -> str:
        return "x"

    provider = _NoToolsProvider("gemini-x")
    with pytest.raises(ToolUseUnsupportedError):
        await provider.complete_with_tools(
            user_text="do it",
            system_text=None,
            tool_schemas=[{"name": "shell"}],  # non-empty → must fail loud
            tool_dispatcher=_dispatch,
        )
