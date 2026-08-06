"""ADR-19 #4 — the experiment's label must actually reach the database.

The D05.2 lesson, applied ahead of time: a module can be perfect and never
wired. If `lessons_arm` is not persisted, the hold-out still degrades a fifth of
sessions and answers nothing — strictly worse than not running the experiment.
"""

from __future__ import annotations

import pytest

from stackowl.infra.lesson_experiment import ARM_HELD_OUT, ARM_INJECTED, set_arm
from stackowl.memory.outcome_store import TaskOutcomeStore


async def _arm_in_db(db, trace: str) -> str | None:
    rows = await db.fetch_all(
        "SELECT lessons_arm FROM task_outcomes WHERE trace_id = ?", (trace,)
    )
    return None if not rows else rows[0]["lessons_arm"]


@pytest.mark.asyncio
async def test_a_held_out_turn_is_RECORDED_as_held_out(tmp_db):
    store = TaskOutcomeStore(tmp_db)
    set_arm(ARM_HELD_OUT)
    try:
        await store.record(
            trace_id="t-held", session_key="s", owl_name="o", channel="cli",
            success=True, latency_ms=1.0, tool_call_count=0,
            failure_class=None, step_durations={},
            input_text="q", response_text="a",
        )
    finally:
        set_arm(ARM_INJECTED)

    assert await _arm_in_db(tmp_db, "t-held") == ARM_HELD_OUT


@pytest.mark.asyncio
async def test_an_ordinary_turn_is_recorded_as_the_control(tmp_db):
    store = TaskOutcomeStore(tmp_db)
    set_arm(ARM_INJECTED)
    await store.record(
        trace_id="t-inj", session_key="s", owl_name="o", channel="cli",
        success=True, latency_ms=1.0, tool_call_count=0,
        failure_class=None, step_durations={},
        input_text="q", response_text="a",
    )

    assert await _arm_in_db(tmp_db, "t-inj") == ARM_INJECTED


@pytest.mark.asyncio
async def test_the_arm_is_read_from_the_carrier_not_a_parameter(tmp_db):
    """record() has no `lessons_arm` argument on purpose — classify decides the
    arm and this recorder is several hops away, so it travels by ContextVar
    exactly like prompt_metrics (D01.6). This pins that seam: if someone
    'helpfully' adds a parameter that defaults to injected, held-out turns start
    recording as control and the experiment silently inverts."""
    import inspect

    sig = inspect.signature(TaskOutcomeStore.record)
    assert "lessons_arm" not in sig.parameters
