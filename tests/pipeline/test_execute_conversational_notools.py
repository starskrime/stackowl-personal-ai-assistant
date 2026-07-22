"""T3 — conversational turns take the zero-tools plain-stream path.

``execute.run`` must:
  * for ``intent_class="conversational"``: skip ``_run_with_tools`` entirely,
    even when ``tool_registry.all()`` is non-empty.
  * for ``intent_class="standard"``: enter ``_run_with_tools`` as before.

The test monkeypatches ``exe._run_with_tools`` so we can observe which branch
was taken without running the real tool loop or a real provider.  The plain-stream
path needs a provider with a working ``stream()`` — we supply one here.

Harness mirrors ``tests/pipeline/steps/test_execute_budget.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import execute as exe
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Minimal tool — gives tool_registry.all() a non-empty result
# ---------------------------------------------------------------------------


class _DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy_tool"

    @property
    def description(self) -> str:
        return "Dummy tool for conversational no-tools test."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="dummy_tool",
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="dummy", duration_ms=1.0)


# ---------------------------------------------------------------------------
# Scripted provider — stream() yields one token (plain-stream path)
# ---------------------------------------------------------------------------


class _StreamingProvider:
    """Provider whose stream() yields a single token for the plain-stream path."""

    protocol = "anthropic"

    async def stream(  # noqa: ANN201
        self,
        messages: list[Any],
        model: str,
        **kwargs: object,
    ):
        yield "hello"

    async def complete_with_tools(  # pragma: no cover
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, object]],
        tool_dispatcher: Any,
        history: list[Any] | None = None,
        on_iteration_complete: Any = None,
        **_kwargs: object,
    ) -> tuple[str, list[dict[str, Any]]]:
        return ("should not be called", [])


class _FakeProviderRegistry:
    def __init__(self, provider: _StreamingProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _StreamingProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _StreamingProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _StreamingProvider:
        return self._p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(intent: str) -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="execute",
        intent_class=intent,  # type: ignore[arg-type]
        system_prompt="You are a helper.",
    )


def _make_services() -> StepServices:
    provider = _StreamingProvider()
    tool_registry = ToolRegistry()
    tool_registry.register(_DummyTool())

    owl_registry = OwlRegistry.with_default_secretary()

    return StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=tool_registry,
        owl_registry=owl_registry,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversational_does_not_enter_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A conversational turn must NOT call _run_with_tools even with tools registered."""
    entered: dict[str, bool] = {"tools": False}

    async def _spy(*a: object, **k: object) -> Any:
        entered["tools"] = True
        return a[0]  # return state unchanged

    monkeypatch.setattr(exe, "_run_with_tools", _spy)

    stoken = set_services(_make_services())
    try:
        await exe.run(_make_state("conversational"))
    finally:
        reset_services(stoken)

    assert entered["tools"] is False, (
        "conversational turn incorrectly entered _run_with_tools"
    )


class _LeakyProvider:
    """Provider whose stream() yields a leaked, unparsed ACTION-style tool call —
    reproduces the reported bug: a plain conversational turn (no tool loop)
    where the model still emitted a malformed tool-call attempt as its final
    answer, and nothing caught it before delivery."""

    protocol = "anthropic"

    async def stream(self, messages, model, **kwargs):  # noqa: ANN001, ANN201
        yield "Let me check that.\n\n"
        yield 'ACTION: <function_name>cronjob</function_name>\n<parameter=action>\nlist\n</parameter>'


@pytest.mark.asyncio
async def test_conversational_leaked_tool_call_is_floored_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plain-stream path had no leak guard at all (unlike complete_with_tools,
    which already refuses to deliver an unparsed ACTION block). Assert the raw
    leak never reaches state.responses and a step_errors entry is recorded
    instead."""
    services = StepServices(
        provider_registry=_FakeProviderRegistry(_LeakyProvider()),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    stoken = set_services(services)
    try:
        result = await exe.run(_make_state("conversational"))
    finally:
        reset_services(stoken)

    delivered = "".join(r.content for r in result.responses)
    assert "ACTION:" not in delivered
    assert "function_name" not in delivered
    assert any(e.step == "execute" for e in result.step_errors)


class _StuckLoopProvider:
    """Provider whose stream() repeats the SAME short unit hundreds of times —
    reproduces the live incident (2026-07-16): the model got stuck emitting
    empty ``<tool_code></tool_code>`` pairs ~250 times (~1000 deltas) instead
    of a real answer. This shape matches NEITHER ``ACTION:`` nor a native
    ``name{...}`` call — no syntax-specific regex catches it; only a general
    repetition guard does."""

    protocol = "anthropic"

    async def stream(self, messages, model, **kwargs):  # noqa: ANN001, ANN201
        for _ in range(300):
            yield "<tool_code>"
            yield "\n"
            yield "</tool_code>"
            yield "\n"


@pytest.mark.asyncio
async def test_conversational_degenerate_repetition_is_floored_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck-loop stream (same short unit repeated far past any legitimate
    answer) must be floored, not shipped raw as ~1000+ chunks to the user."""
    services = StepServices(
        provider_registry=_FakeProviderRegistry(_StuckLoopProvider()),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    stoken = set_services(services)
    try:
        result = await exe.run(_make_state("conversational"))
    finally:
        reset_services(stoken)

    delivered = "".join(r.content for r in result.responses)
    assert "tool_code" not in delivered
    assert any(e.step == "execute" for e in result.step_errors)
    # Must catch it FAR short of the full 300-repeat stream, not after burning
    # through every repetition to the end.
    assert len(delivered) < 100


class _CapturingProvider:
    """Provider whose stream() records the kwargs it was called with, so a
    test can assert on the max_tokens override without a real provider call."""

    protocol = "anthropic"
    name = "capturing-test-provider"

    def __init__(self) -> None:
        self.received_kwargs: dict[str, object] | None = None

    async def stream(self, messages, model, **kwargs):  # noqa: ANN001, ANN201
        self.received_kwargs = kwargs
        yield "hi there!"


@pytest.mark.asyncio
async def test_conversational_plain_stream_gets_small_max_tokens_cap() -> None:
    """Live incident 2026-07-22: a 'hi' conversational turn was given the
    provider's full default output budget (up to 250000 tokens) on the
    plain-stream path and a verbose model used 7951 of them, taking 205s.
    The conversational turn must pass a small explicit max_tokens override
    to provider.stream() instead of relying on the provider's own default."""
    from stackowl.pipeline.steps.execute import _CONVERSATIONAL_MAX_TOKENS

    provider = _CapturingProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    stoken = set_services(services)
    try:
        await exe.run(_make_state("conversational"))
    finally:
        reset_services(stoken)

    assert provider.received_kwargs is not None
    assert provider.received_kwargs.get("max_tokens") == _CONVERSATIONAL_MAX_TOKENS
    assert provider.received_kwargs.get("disable_thinking") is True


@pytest.mark.asyncio
async def test_standard_plain_stream_does_not_cap_max_tokens() -> None:
    """A 'standard' turn that reaches the plain-stream path (e.g. no tools
    registered) must NOT get the conversational cap — it may legitimately
    need the provider's full default output budget."""
    provider = _CapturingProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),  # empty — no tools registered
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    stoken = set_services(services)
    try:
        await exe.run(_make_state("standard"))
    finally:
        reset_services(stoken)

    assert provider.received_kwargs is not None
    assert "max_tokens" not in provider.received_kwargs


@pytest.mark.asyncio
async def test_standard_enters_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A standard turn MUST call _run_with_tools when tools are registered."""
    entered: dict[str, bool] = {"tools": False}

    async def _spy(*a: object, **k: object) -> Any:
        entered["tools"] = True
        return a[0]  # return state unchanged

    monkeypatch.setattr(exe, "_run_with_tools", _spy)

    stoken = set_services(_make_services())
    try:
        await exe.run(_make_state("standard"))
    finally:
        reset_services(stoken)

    assert entered["tools"] is True, (
        "standard turn failed to enter _run_with_tools"
    )


class _LongFormAnswerProvider:
    """Provider whose stream() yields a realistic long-form technical answer:
    prose, a numbered list, a markdown table (repeated "---" separator
    deltas), and multiple fenced code blocks (repeated "```" deltas). The
    degenerate-repetition guard's threshold (20) must never trip on
    LEGITIMATE structural repetition — only on a genuinely stuck loop. This
    is the guard's own party-mode review calling out that only the failure
    case had been tested, never the realistic legitimate case."""

    protocol = "anthropic"
    name = "long-form-test-provider"

    async def stream(self, messages, model, **kwargs):  # noqa: ANN001, ANN201
        yield "Here's a rundown of the three approaches:\n\n"
        yield "1. Use a cache — fastest, but stale data risk.\n"
        yield "2. Query live — always fresh, slower.\n"
        yield "3. Hybrid — cache with a short TTL.\n\n"
        yield "| Approach | Speed | Freshness |\n"
        yield "| --- | --- | --- |\n"
        yield "| Cache | Fast | Stale |\n"
        yield "| --- | --- | --- |\n"
        yield "| Live | Slow | Fresh |\n"
        yield "| --- | --- | --- |\n"
        yield "| Hybrid | Medium | Mostly fresh |\n\n"
        for i in range(3):
            yield "```python\n"
            yield f"def approach_{i}():\n    return {i}\n"
            yield "```\n\n"
        yield "The hybrid approach is usually the right default."


@pytest.mark.asyncio
async def test_conversational_long_form_answer_does_not_trip_repetition_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A realistic long-form answer (list + table + repeated code fences) must
    be delivered whole, not floored by the degenerate-repetition guard."""
    services = StepServices(
        provider_registry=_FakeProviderRegistry(_LongFormAnswerProvider()),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    stoken = set_services(services)
    try:
        result = await exe.run(_make_state("conversational"))
    finally:
        reset_services(stoken)

    delivered = "".join(r.content for r in result.responses)
    assert "hybrid approach is usually the right default" in delivered
    assert result.step_errors == ()
