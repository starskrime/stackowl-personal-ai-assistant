"""ClarifyGateway.wait_for_answer records blocked-on-human time (Phase 1A)."""

from __future__ import annotations

import asyncio

import pytest

from stackowl.interaction.clarify_gateway import (
    OUTCOME_ANSWERED,
    OUTCOME_TIMED_OUT,
    ClarifyGateway,
    PendingClarify,
)
from stackowl.pipeline.budget import human_wait


class _StepClock:
    """Returns successive values from a list on each call (injected time_fn)."""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self._i = 0

    def __call__(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


@pytest.mark.asyncio
async def test_resolve_before_park_records_wait_duration() -> None:
    # time_fn called twice in wait_for_answer: start, then finally.
    gw = ClarifyGateway(time_fn=_StepClock([100.0, 105.0]))
    ev = asyncio.Event()
    ev.set()  # already resolved → skip await, but still measured
    gw._pending["c1"] = PendingClarify(
        clarify_id="c1", session_id="s", channel="cli",
        question="q", answer="yes", event=ev,
    )
    token = human_wait.bind()
    try:
        answer, outcome = await gw.wait_for_answer("c1", timeout=10.0)
        assert (answer, outcome) == ("yes", OUTCOME_ANSWERED)
        assert human_wait.current_human_wait_seconds() == 5.0
    finally:
        human_wait.reset(token)


@pytest.mark.asyncio
async def test_timeout_path_still_records_wait() -> None:
    # Event never set → asyncio.wait_for times out; finally must still record.
    gw = ClarifyGateway(time_fn=_StepClock([200.0, 203.0]))
    ev = asyncio.Event()  # not set
    gw._pending["c2"] = PendingClarify(
        clarify_id="c2", session_id="s", channel="cli",
        question="q", event=ev,
    )
    token = human_wait.bind()
    try:
        answer, outcome = await gw.wait_for_answer("c2", timeout=0.01)
        assert outcome == OUTCOME_TIMED_OUT
        assert answer is None
        assert human_wait.current_human_wait_seconds() == 3.0
    finally:
        human_wait.reset(token)


@pytest.mark.asyncio
async def test_no_parked_entry_records_nothing() -> None:
    gw = ClarifyGateway(time_fn=_StepClock([1.0, 99.0]))
    token = human_wait.bind()
    try:
        answer, outcome = await gw.wait_for_answer("missing", timeout=1.0)
        assert (answer, outcome) == (None, OUTCOME_TIMED_OUT)
        # Early return BEFORE the timed block → no wait recorded.
        assert human_wait.current_human_wait_seconds() == 0.0
    finally:
        human_wait.reset(token)
