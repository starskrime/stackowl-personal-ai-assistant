"""Tests for consolidate.run appending the token-usage line (Epic 3 Task 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk


@pytest.mark.asyncio
async def test_token_line_appended_when_records_exist(monkeypatch):
    _cost_tracker = MagicMock()
    _cost_tracker.get_turn_token_totals = AsyncMock(return_value=(600, 320))

    class FakeServices:
        cost_tracker = _cost_tracker
        approach_rating_tracker = None  # Epic 2 field, not under test here

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t1", session_id="s1", input_text="hi", channel="cli", owl_name="secretary",
        pipeline_step="consolidate",
        responses=(ResponseChunk(
            content="here is the answer", is_final=False, chunk_index=0,
            trace_id="t1", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "here is the answer\n\n\U0001F522 600 in / 320 out"


@pytest.mark.asyncio
async def test_no_token_line_when_no_records(monkeypatch):
    _cost_tracker = MagicMock()
    _cost_tracker.get_turn_token_totals = AsyncMock(return_value=None)

    class FakeServices:
        cost_tracker = _cost_tracker
        approach_rating_tracker = None

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t2", session_id="s1", input_text="hi", channel="cli", owl_name="secretary",
        pipeline_step="consolidate",
        responses=(ResponseChunk(
            content="here is the answer", is_final=False, chunk_index=0,
            trace_id="t2", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "here is the answer"


@pytest.mark.asyncio
async def test_no_token_line_on_floor_chunk(monkeypatch):
    _cost_tracker = MagicMock()
    _cost_tracker.get_turn_token_totals = AsyncMock(return_value=(600, 320))

    class FakeServices:
        cost_tracker = _cost_tracker
        approach_rating_tracker = None

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="t3", session_id="s1", input_text="hi", channel="cli", owl_name="secretary",
        pipeline_step="consolidate",
        responses=(ResponseChunk(
            content="I couldn't complete this", is_final=False, chunk_index=0,
            trace_id="t3", owl_name="secretary", is_floor=True,
        ),),
    )

    result = await consolidate.run(state)

    assert result.responses[-1].content == "I couldn't complete this"
    _cost_tracker.get_turn_token_totals.assert_not_called()
