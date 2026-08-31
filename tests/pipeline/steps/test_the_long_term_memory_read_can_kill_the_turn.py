"""The one gather in classify that a sick store can take the whole turn down with.

MEASURED 2026-08-31 by reading the function. ``classify.run`` assembles a turn's
context from four sources, and three of them degrade::

    _gather_history           try / except -> []
    _gather_graph_context     try / except -> ""
    _gather_preferences       try / except -> ""   ("Failures ... return ''")

    context = await bridge.retrieve(...)          <- no guard at all

``retrieve`` is the LONG-TERM committed-fact read — the one carrying what the user
actually told the platform. It is the only unguarded one, and it is the most
important: a raised exception there does not degrade the turn, it ends it.

AND THE CONVENTION FOR SAYING SO ALREADY EXISTS, TWICE. F-49 introduced
``_REFLECTIONS_DEGRADED_BLOCK`` and ``_LESSONS_DEGRADED_BLOCK``, both wired
(classify.py:272 and :543), both with the same reasoning written out: "a search
failure here must not silently look identical to 'no lessons exist'". Long-term
memory had neither the guard nor the block, so a failed recall would have made the
model answer as though the operator had never told it anything.

WHY A GUARD AND NOT THE CIRCUIT BREAKER THAT WAS ASKED FOR. Bakir's phase-5 order
is "circuit breaker, prefetch, flush barrier", and the research note frames the
first as "a circuit breaker around memory reads so a sick store degrades instead of
failing the turn". Measured across four days of logs: ZERO memory-read failures. A
breaker exists to stop hammering a sick backend; with nothing hammering, its state,
thresholds and half-open probes would be exactly the "abstraction for a scale that
never arrived" the same research note cites when it DECLINED the provider ABC. The
degradation is the part that is missing; see ESC-92.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify

pytestmark = pytest.mark.asyncio


class _SickStore:
    """A memory store that is up enough to be called and broken enough to raise."""

    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, query: str, session_key: str) -> str:
        self.calls += 1
        raise RuntimeError("database is locked")

    async def get_recent_turns(self, *a: object, **k: object) -> list:
        return []


class _HealthyStore(_SickStore):
    async def retrieve(self, query: str, session_key: str) -> str:
        self.calls += 1
        return "## Long-term memory\nThe user's dentist is Dr Antoon."


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t-1", session_key="owl:secretary:telegram:dm:72055773",
        conversation_id="c-1", input_text="what did I tell you about my dentist?",
        channel="telegram", owl_name="secretary", pipeline_step="classify",
    )


async def _run(store: object) -> PipelineState:
    # `conversation_store` is a PROPERTY over `memory_bridge`, deliberately not a
    # second field (services.py:258) — so the fixture sets the one that exists.
    token = set_services(StepServices(memory_bridge=store))  # type: ignore[arg-type]
    try:
        return await classify.run(_state())
    finally:
        from stackowl.pipeline.services import reset_services

        reset_services(token)


async def test_a_SICK_store_does_not_end_the_turn() -> None:
    """The defect. Every sibling gather degrades; this one raised."""
    store = _SickStore()

    out = await _run(store)

    assert out is not None, "a memory-store failure ended the turn"
    assert store.calls == 1


async def test_the_turn_is_TOLD_its_long_term_memory_is_unavailable() -> None:
    """F-49's rule, third instance: a failure must not look identical to "the user
    has never told me anything"."""
    out = await _run(_SickStore())

    assert "DEGRADED" in (out.memory_context or ""), (
        "the model would answer as though the operator had told it nothing"
    )


async def test_a_HEALTHY_store_is_unchanged() -> None:
    """The expensive direction — a guard that swallows real context is worse than
    the crash it prevents."""
    out = await _run(_HealthyStore())

    assert "Dr Antoon" in (out.memory_context or "")
    assert "DEGRADED" not in (out.memory_context or "")


async def test_the_failure_is_LOUD(caplog: pytest.LogCaptureFixture) -> None:
    """No hidden errors. Production runs at INFO, and a degraded long-term memory
    is the kind of thing that must be findable after the fact."""
    with caplog.at_level(logging.INFO):
        await _run(_SickStore())

    records = [
        r for r in caplog.records
        if "DEGRADED" in r.getMessage() and r.levelno >= logging.WARNING
    ]
    assert records, "the store failed and nothing said so"


async def test_the_degraded_block_matches_the_two_that_already_exist() -> None:
    """One vocabulary. A third phrasing for the same idea is how a reader learns to
    ignore all three."""
    from stackowl.pipeline.steps.classify import (
        _LESSONS_DEGRADED_BLOCK,
        _LONG_TERM_DEGRADED_BLOCK,
        _REFLECTIONS_DEGRADED_BLOCK,
    )

    for block in (_REFLECTIONS_DEGRADED_BLOCK, _LESSONS_DEGRADED_BLOCK):
        assert block.startswith("## ") and "DEGRADED" in block.splitlines()[0]
    assert _LONG_TERM_DEGRADED_BLOCK.startswith("## ")
    assert "DEGRADED" in _LONG_TERM_DEGRADED_BLOCK.splitlines()[0]
    assert "do not assume" in _LONG_TERM_DEGRADED_BLOCK
