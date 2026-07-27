"""D01.1 — lessons must not change with the question.

Measured 2026-07-27 on two real turns: prefs_len was 252 on BOTH (genuinely
stable) while lessons_len was 1097 then 765. Lessons were recalled by relevance
to the user's message, exactly like the memory recall this item moved out of the
prompt — so keeping them in the prompt made invariant I1 unreachable.

Removing them was rejected: they are the output of the reflect->recall arc, the
model cannot be relied on to call a tool to fetch its OWN past lessons, and the
NFR-4 gateway test exists to protect that. Bakir's call (2026-07-27) was to make
them query-INDEPENDENT and keep them.

DEVIATION, stated deliberately. He phrased the mechanism as "the N most recent or
highest-weighted". The index is LanceDB and exposes ANN only — a true recency or
weight ordering needs a new scan path through both LessonsIndex and
LessonsLanceAdapter. What I1 actually requires is the PROPERTY (identical every
turn of a session), not that particular mechanism, so the selection key is the
OWL's identity instead of the user's message. Same owl, same index contents, same
lessons — and "lessons relevant to this owl" is a more defensible selection than
"lessons similar to whatever was just typed". If recency is wanted specifically,
that is a follow-up with a real adapter change behind it.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.steps.classify import _gather_lessons

pytestmark = pytest.mark.asyncio


class _RecordingIndex:
    """Captures the string the lessons channel is selected by."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 5, source_filter: object = None):
        self.queries.append(query)
        return []


async def test_the_users_message_does_not_select_the_lessons() -> None:
    """The defect, directly: two different questions must not produce two
    different lesson sets, or the prompt can never be byte-identical."""
    idx = _RecordingIndex()
    set_services(StepServices(lessons_index=idx))  # type: ignore[arg-type]

    await _gather_lessons("how do I deploy?", owl_name="secretary")
    await _gather_lessons("what is the capital of France?", owl_name="secretary")

    assert len(idx.queries) == 2
    assert idx.queries[0] == idx.queries[1], (
        "the selection key must not vary with the user's question"
    )
    assert "deploy" not in idx.queries[0]
    assert "capital of France" not in idx.queries[1]


async def test_a_different_owl_selects_differently() -> None:
    """Stable is not the same as constant. Each owl should carry ITS lessons —
    otherwise the block is stable and useless."""
    idx = _RecordingIndex()
    set_services(StepServices(lessons_index=idx))  # type: ignore[arg-type]

    await _gather_lessons("anything", owl_name="secretary")
    await _gather_lessons("anything", owl_name="scout")

    assert idx.queries[0] != idx.queries[1]


async def test_no_index_wired_is_silent_not_fatal() -> None:
    """Invariant I2 — a channel that cannot answer degrades to absence."""
    set_services(StepServices())

    assert await _gather_lessons("anything", owl_name="secretary") == ""
