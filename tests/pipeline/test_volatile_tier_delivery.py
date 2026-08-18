"""D01.1 stage 2 — the volatile tier leaves the system prompt and rides the turn.

Stage 1 named the tiers. This moves the callers: `assemble` builds only the
STABLE tier, and `execute` prefixes each turn's user text with the VOLATILE one.

The point is not tidiness. The wall-clock is rendered to the minute
(`strftime("… at %I:%M %p …")`), so while it sat in the system prompt the prompt
could never be byte-identical across turns a minute apart — the primary reason
invariant I1 was unreachable (DEBT-23). Freezing it instead would have told the
model a time up to ~24h stale, defeating what it is for.

Both halves land together on purpose: `assemble` alone would strip the clock and
give the model nothing, which is a capability removal rather than a refactor.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import assemble
from stackowl.pipeline.steps.execute import _turn_context_prefix

NOW = datetime.datetime(2026, 7, 27, 21, 46, tzinfo=datetime.UTC)


def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t-vol-1", session_key="owl:secretary:cli:dm:1", input_text="hi",
        channel="cli", owl_name="secretary", pipeline_step="assemble",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_system_prompt_no_longer_carries_a_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-23, closed at the source. A minute-resolution stamp in the frozen
    tier makes byte-identical prompts impossible by construction."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices())

    prompt = (await assemble.run(_state())).system_prompt or ""

    assert "Right now it is" not in prompt


@pytest.mark.asyncio
async def test_two_assembles_a_minute_apart_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property I1 needs, and the one the clock made unreachable."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices())

    first = (await assemble.run(_state())).system_prompt
    second = (await assemble.run(_state())).system_prompt

    assert first == second


@pytest.mark.asyncio
async def test_the_system_prompt_still_teaches_the_call_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-22, answered structurally: the protocol is STABLE, so it survives
    freezing. A per-turn conditional could not — a session opening with "hi"
    would carry a protocol-less prompt for its whole life."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices())

    for intent in ("standard", "conversational"):
        prompt = (await assemble.run(_state(intent_class=intent))).system_prompt or ""
        assert "ACTION:" in prompt, f"protocol missing for intent_class={intent}"


def test_the_turn_carries_the_clock_instead() -> None:
    """Moved, not deleted — the model still knows the real time, and now knows
    it accurately on every turn rather than as of whenever the prompt froze."""
    prefixed = _turn_context_prefix(_state(), now=NOW)

    assert "Right now it is" in prefixed
    assert "hi" in prefixed, "the user's actual message survives the prefix"


def test_a_tool_free_turn_still_gets_its_negative_instruction() -> None:
    """The `else` branch was never silence: it actively tells the model not to
    invent a call, because silence did not stop a natively tool-trained model
    attempting `default_api:search{…}` on a turn offering nothing. That claim is
    about ONE turn, so it belongs here rather than in the frozen tier."""
    free = _turn_context_prefix(_state(intent_class="conversational"), now=NOW)
    working = _turn_context_prefix(_state(intent_class="standard"), now=NOW)

    assert "No capabilities are available to you this turn" in free
    assert "No capabilities are available to you this turn" not in working
