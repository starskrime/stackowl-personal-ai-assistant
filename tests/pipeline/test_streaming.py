"""ResponseChunk.actions — tappable follow-up actions carried from a
slash-command CommandResponse (see startup/orchestrator.py::_deliver_command_stub)."""

from __future__ import annotations

from typing import Any

import pytest


def test_response_chunk_actions_defaults_empty() -> None:
    from stackowl.pipeline.streaming import ResponseChunk

    chunk = ResponseChunk(content="hi", is_final=True, chunk_index=0, trace_id="t1", owl_name="system")
    assert chunk.actions == ()


def test_response_chunk_carries_actions() -> None:
    from stackowl.commands.response import Action
    from stackowl.pipeline.streaming import ResponseChunk

    chunk = ResponseChunk(
        content="pick one", is_final=True, chunk_index=0, trace_id="t1", owl_name="system",
        actions=(Action(label="Go", command="/help"),),
    )
    assert len(chunk.actions) == 1


# ---------------------------------------------------------------------------
# LiteLLM-hang regression — a plain-stream call that completes cleanly with
# zero content chars must surface as a clear failure, not a silent empty
# response. Harness mirrors tests/pipeline/test_execute_conversational_notools.py.
# ---------------------------------------------------------------------------


class _EmptyStreamProvider:
    """Provider whose stream() completes cleanly with zero chunks/chars."""

    name = "empty-stream"
    protocol = "anthropic"

    async def stream(  # noqa: ANN201
        self,
        messages: list[Any],
        model: str,
        **kwargs: object,
    ):
        return
        yield  # pragma: no cover — makes this an async generator


class _FakeProviderRegistry:
    def __init__(self, provider: _EmptyStreamProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _EmptyStreamProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _EmptyStreamProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _EmptyStreamProvider:
        return self._p


@pytest.mark.asyncio
async def test_conversational_empty_stream_surfaces_as_owl_timeout_error() -> None:
    """A stream that completes with zero content chars must not produce a
    silent empty response — execute.run() converts it into an OwlTimeoutError,
    handled by the existing except-clause into a clear step_errors entry."""
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute as exe

    provider = _EmptyStreamProvider()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=None,
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    state = PipelineState(
        trace_id="t",
        session_id="s",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="execute",
        intent_class="conversational",  # type: ignore[arg-type]
        system_prompt="You are a helper.",
    )

    stoken = set_services(services)
    try:
        out = await exe.run(state)
    finally:
        reset_services(stoken)

    assert any(se.exc_type == "OwlTimeoutError" for se in out.step_errors), (
        f"expected an OwlTimeoutError step_error for a zero-char stream, got: {out.step_errors}"
    )
    assert out.responses == ()
