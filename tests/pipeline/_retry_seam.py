"""Drive the REAL persist_turn over a floored turn, faking only the store.

The point of these tests is the seam where a floored turn decides what to write,
so persist_turn itself runs unmodified — only the durable task store is a double.
A test that reimplemented the decision would prove nothing about the code that
actually produced 5,766 retry_queue rows.
"""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


async def run_floored_turn(
    store: Any,
    monkeypatch: Any,
    *,
    trace_id: str = "t1",
    session_key: str = "owl:secretary:telegram:dm:72055773",
    channel: str = "telegram",
    reply_target: int | str | None = 72055773,
    retry_replay: bool = False,
) -> None:
    """Run one floored turn through persist_turn and return."""
    state = PipelineState(
        trace_id=trace_id,
        session_key=session_key,
        input_text="find me remote staff SWE roles",
        channel=channel,
        owl_name="secretary",
        pipeline_step="deliver",
        reply_target=reply_target,
        retry_replay=retry_replay,
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this.",
                is_final=True,
                chunk_index=0,
                trace_id=trace_id,
                owl_name="secretary",
                is_floor=True,
            ),
        ),
    )
    token = set_services(StepServices(durable_task_store=store))
    try:
        await persist_turn(state)
    finally:
        reset_services(token)
