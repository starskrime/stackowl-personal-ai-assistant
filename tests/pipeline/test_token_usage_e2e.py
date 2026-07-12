"""Full loop: multiple cost_records rows for a trace -> summed total appended to the final answer.

Epic 3 Task 3 (token-usage-display) — end-to-end regression covering Task 1
(CostTracker.get_turn_token_totals) and Task 2 (consolidate token-line append)
against a real migration-built DB, real CostTracker, and real consolidate.run.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import consolidate
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.cost_tracker import CostTracker


@pytest.mark.asyncio
async def test_full_token_display_loop(tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch) -> None:
    await tmp_db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, "
        "cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "classifier", 50, 5, 0.0001, "trace-e2e", "2026-07-12T00:00:00"),
    )
    await tmp_db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens, "
        "cost_usd, trace_id, recorded_at) VALUES (?,?,?,?,?,?,?)",
        ("openai", "answer", 400, 250, 0.008, "trace-e2e", "2026-07-12T00:00:02"),
    )

    _cost_tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    class FakeServices:
        cost_tracker = _cost_tracker
        approach_rating_tracker = None

    monkeypatch.setattr("stackowl.pipeline.steps.consolidate.get_services", lambda: FakeServices())

    state = PipelineState(
        trace_id="trace-e2e", session_id="s1", input_text="prepare me for the interview",
        channel="telegram", owl_name="secretary", pipeline_step="consolidate",
        responses=(ResponseChunk(
            content="here's your interview prep plan", is_final=False, chunk_index=0,
            trace_id="trace-e2e", owl_name="secretary", is_floor=False,
        ),),
    )

    result = await consolidate.run(state)

    # content (what persist_turn stores) stays clean; the token line is
    # display-only chrome carried on display_suffix.
    assert result.responses[-1].content == "here's your interview prep plan"
    assert result.responses[-1].display_suffix == "\n\n\U0001F522 450 in / 255 out"
