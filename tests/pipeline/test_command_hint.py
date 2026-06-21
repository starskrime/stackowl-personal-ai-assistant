"""Tests for surface_command_hint — additive NL→command hint + routing notice."""

from __future__ import annotations

import types

import pytest

from stackowl.commands.resolver import CommandCandidate
from stackowl.pipeline.command_hint import surface_command_hint
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


class _FakeResolver:
    def __init__(self, candidates: list[CommandCandidate]) -> None:
        self._candidates = candidates
        self.calls = 0

    async def resolve(self, query: str, *, limit: int = 5) -> list[CommandCandidate]:
        self.calls += 1
        return self._candidates[:limit]


def _settings(*, command_hints: bool) -> object:
    return types.SimpleNamespace(ui=types.SimpleNamespace(command_hints=command_hints))


def _state(
    *,
    input_text: str = "forget what I said about my sister",
    answer: str = "Done, I removed that note.",
    floor: bool = False,
    interactive: bool = True,
    route_suggestion: str | None = None,
) -> PipelineState:
    chunk = ResponseChunk(
        content=answer,
        is_final=False,
        chunk_index=0,
        trace_id="t1",
        owl_name="secretary",
        is_floor=floor,
    )
    return PipelineState(
        trace_id="t1",
        session_id="s1",
        input_text=input_text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="surface",
        interactive=interactive,
        route_suggestion=route_suggestion,
        responses=(chunk,),
    )


def _services(resolver: object | None, *, command_hints: bool) -> StepServices:
    return StepServices(
        settings=_settings(command_hints=command_hints),  # type: ignore[arg-type]
        command_hint_resolver=resolver,  # type: ignore[arg-type]
    )


async def test_no_op_when_feature_off() -> None:
    resolver = _FakeResolver([CommandCandidate("/memory forget", "drop a fact", 0.9, "verb")])
    state = _state()
    out = await surface_command_hint(state, _services(resolver, command_hints=False))
    assert out is state  # untouched
    assert resolver.calls == 0  # resolver not even consulted


async def test_high_confidence_appends_marked_hint() -> None:
    resolver = _FakeResolver([CommandCandidate("/memory forget", "drop a fact", 0.9, "verb")])
    out = await surface_command_hint(_state(), _services(resolver, command_hints=True))
    assert len(out.responses) == 2
    hint = out.responses[-1].content
    assert "☆ tip" in hint
    assert "/memory forget" in hint
    assert out.responses[-1].is_final is False


async def test_low_confidence_no_hint() -> None:
    resolver = _FakeResolver([CommandCandidate("/memory forget", "drop a fact", 0.20, "verb")])
    out = await surface_command_hint(_state(), _services(resolver, command_hints=True))
    assert len(out.responses) == 1  # below the high floor → nothing appended


async def test_no_hint_on_floored_answer() -> None:
    # A floored/failed turn must never be decorated with a tip.
    resolver = _FakeResolver([CommandCandidate("/memory forget", "drop a fact", 0.9, "verb")])
    out = await surface_command_hint(
        _state(floor=True), _services(resolver, command_hints=True)
    )
    assert len(out.responses) == 1
    assert resolver.calls == 0


async def test_slash_input_is_not_prose() -> None:
    # An explicit slash command turn is not natural language → no resolver hint.
    resolver = _FakeResolver([CommandCandidate("/memory forget", "drop a fact", 0.9, "verb")])
    out = await surface_command_hint(
        _state(input_text="/memory forget"),
        _services(resolver, command_hints=True),
    )
    assert len(out.responses) == 1
    assert resolver.calls == 0


async def test_route_suggestion_surfaced() -> None:
    # The previously-dead RouteDecision.suggestion is consumed and shown.
    out = await surface_command_hint(
        _state(route_suggestion="Did you mean @max? — routing to @max."),
        _services(None, command_hints=True),
    )
    assert len(out.responses) == 2
    assert "Did you mean @max" in out.responses[-1].content


async def test_route_suggestion_and_command_hint_both() -> None:
    resolver = _FakeResolver([CommandCandidate("/memory forget", "drop a fact", 0.9, "verb")])
    out = await surface_command_hint(
        _state(route_suggestion="routing to @secretary."),
        _services(resolver, command_hints=True),
    )
    # routing notice + command tip = two appended lines.
    assert len(out.responses) == 3
    joined = " ".join(c.content for c in out.responses[1:])
    assert "@secretary" in joined
    assert "/memory forget" in joined


async def test_never_raises_on_resolver_error() -> None:
    class _Boom:
        async def resolve(self, query: str, *, limit: int = 5) -> list[CommandCandidate]:
            raise RuntimeError("boom")

    out = await surface_command_hint(_state(), _services(_Boom(), command_hints=True))
    # Swallowed — original response intact.
    assert len(out.responses) == 1
