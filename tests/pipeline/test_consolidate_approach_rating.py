"""Tests for consolidate.run attaching the approach-rating keyboard (Task 5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk


@pytest.mark.asyncio
async def test_qualifying_answer_gets_rating_keyboard(monkeypatch):
    tracker = MagicMock()
    tracker.record_pending = MagicMock()
    tracker.build_keyboard = MagicMock(
        return_value={"inline_keyboard": [[{"text": "x", "callback_data": "apr:t1:positive"}]]}
    )

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    long_answer = "x" * 250
    state = PipelineState(
        trace_id="t1", session_id="s1", input_text="hi", channel="cli", owl_name="secretary",
        pipeline_step="consolidate",
        responses=(ResponseChunk(
            content=long_answer, is_final=False, chunk_index=0,
            trace_id="t1", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    tracker.record_pending.assert_called_once_with(trace_id="t1")
    assert result.responses[-1].raw_keyboard is not None


@pytest.mark.asyncio
async def test_short_answer_gets_no_keyboard(monkeypatch):
    tracker = MagicMock()

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t2", session_id="s1", input_text="hi", channel="cli", owl_name="secretary",
        pipeline_step="consolidate",
        responses=(ResponseChunk(
            content="ok", is_final=False, chunk_index=0,
            trace_id="t2", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    tracker.record_pending.assert_not_called()
    assert result.responses[-1].raw_keyboard is None


@pytest.mark.asyncio
async def test_floor_answer_gets_no_keyboard(monkeypatch):
    tracker = MagicMock()

    class FakeServices:
        approach_rating_tracker = tracker

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t3", session_id="s1", input_text="hi", channel="cli", owl_name="secretary",
        pipeline_step="consolidate",
        responses=(ResponseChunk(
            content="x" * 250, is_final=False, chunk_index=0,
            trace_id="t3", owl_name="secretary", is_floor=True,
        ),),
    )

    result = await consolidate.run(state)

    tracker.record_pending.assert_not_called()
    assert result.responses[-1].raw_keyboard is None
