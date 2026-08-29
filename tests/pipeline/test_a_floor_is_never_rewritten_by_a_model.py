"""An honesty floor must reach the user as written — no model may re-author it.

FOUND LIVE, 2026-08-29T17:25Z, and it produced a confident false claim.

The overclaim gate floored a turn because `send_message` looked unfulfilled, and
built the message with `_floor_chunk` — whose docstring promises "Pure,
deterministic, no model call" (`delivery_gate.py:271`). `synthesize_floor` renders
a template that QUOTES THE GOAL back:

    I couldn't fully complete this: {goal}. The capability that failed:
    {failed_capability}. ...

The goal was a `/skill use` turn prompt, which itself contained the conditional
sentence 'If no skill named "channel-fallback" exists, say so plainly'. Four steps
later `deliver` fed all 635 characters of that floor to a fast-tier LLM
(`_summarize_if_terse`, "Compress `text` via a fast-tier LLM"), which compressed
635 -> 515 by reading the quoted CONDITIONAL as a FACT. The user received:

    "I could not complete your request... no skill by that name exists."

The skill exists — builtin, enabled, on disk, and `skill_view.execute: exit
{success: True, skill: channel-fallback, source: builtin}` is in the same trace.
The floor never mentioned a missing skill; it said `send_message` failed.

The worst part is the marking: `deliver` re-stamps `is_floor=True` on the rewritten
chunk, so model-authored prose is preserved AS a deterministic honesty floor. Every
downstream reader that trusts `is_floor` is then trusting an LLM's paraphrase of a
guarantee.

A floor is the platform's promise not to overclaim. Running a language model over
it is how that promise became a fabrication.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, content: str, is_floor: bool) -> PipelineState:
    return PipelineState(
        trace_id="t-floor",
        session_key="owl:secretary:telegram:dm:1",
        conversation_id="conv-1",
        input_text="ask",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        responses=(
            ResponseChunk(
                content=content, is_final=True, chunk_index=0,
                trace_id="t-floor", owl_name="secretary", is_floor=is_floor,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_a_floor_is_not_sent_to_a_model_to_be_compressed() -> None:
    """The guard, tested at the seam that actually calls the summariser."""
    from stackowl.pipeline.steps import deliver as deliver_mod

    called: list[str] = []

    async def _spy(text: str, services: object, state: object) -> str:
        called.append(text)
        return "MODEL REWROTE THE FLOOR"

    original = deliver_mod._summarize_if_terse
    deliver_mod._summarize_if_terse = _spy  # type: ignore[assignment]
    try:
        floor_text = (
            "I couldn't fully complete this: [/skill use] ... If no skill named "
            '"channel-fallback" exists, say so plainly. The capability that failed: '
            "send_message." + "x" * 600
        )
        out = await deliver_mod._summarize_unless_floor(
            floor_text, services=None, state=_state(content=floor_text, is_floor=True),
        )
        assert not called, (
            "the floor was handed to a language model — this is the exact path that "
            "turned 'send_message failed' into 'no skill by that name exists'"
        )
        assert out == floor_text, "a floor must reach the user byte-for-byte as written"
    finally:
        deliver_mod._summarize_if_terse = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_an_ORDINARY_answer_is_still_compressed() -> None:
    """The guard must be narrow, or terse mode is silently disabled.

    `length=terse` is a real user preference; skipping compression for every reply
    would be a different bug in the opposite direction.
    """
    from stackowl.pipeline.steps import deliver as deliver_mod

    called: list[str] = []

    async def _spy(text: str, services: object, state: object) -> str:
        called.append(text)
        return "compressed"

    original = deliver_mod._summarize_if_terse
    deliver_mod._summarize_if_terse = _spy  # type: ignore[assignment]
    try:
        prose = "a real answer " * 80
        out = await deliver_mod._summarize_unless_floor(
            prose, services=None, state=_state(content=prose, is_floor=False),
        )
        assert called, "an ordinary answer must still be compressed under length=terse"
        assert out == "compressed"
    finally:
        deliver_mod._summarize_if_terse = original  # type: ignore[assignment]
