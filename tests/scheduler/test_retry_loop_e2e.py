"""Full loop: floor -> retry_queue row -> sweep retries -> success edits message.

End-to-end regression pass for Tasks 1-7 of the failure-retry-loop feature
(retry_queue migration 0082, RetryQueueStore, insert-on-floor hook, Telegram
message_id backfill, RetryActuator, RetrySweepHandler, manual retry-intent
classifier). Runs against a real DbPool with migration 0082 applied (via the
shared ``tmp_db`` fixture, same convention as test_retry_queue_store.py) —
only the AI backend and the Telegram channel adapter are mocked, proving the
store + actuator + sweep handler wiring against the real schema rather than a
hand-typed one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.retry_queue_store import RetryQueueStore
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.handlers.retry_sweep import RetrySweepHandler
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


async def test_full_retry_loop_success(tmp_db: DbPool) -> None:
    store = RetryQueueStore(tmp_db)

    # 1. A floored turn inserts a pending row (turn_persist.py's hook).
    await store.insert_pending(
        trace_id="trace-1", session_id="sess-1", goal="prepare me for the interview",
        banned_capabilities=["cronjob"],
    )
    # 2. The Telegram adapter's send resolves and backfills the sent message ref.
    await store.backfill_channel_message(
        trace_id="trace-1", channel_chat_id=555, channel_message_id=999,
    )

    success_state = PipelineState(
        trace_id="trace-2", session_id="sess-1", input_text="prepare me for the interview",
        channel="telegram", owl_name="secretary", pipeline_step="",
        responses=(
            ResponseChunk(
                content="Here's your plan...", is_final=True, chunk_index=0,
                trace_id="trace-2", owl_name="secretary", is_floor=False,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=success_state)

    adapter = MagicMock()
    adapter.edit_message = AsyncMock()
    channel_registry = MagicMock()
    channel_registry.get = MagicMock(return_value=adapter)

    actuator = RetryActuator(backend=backend, channel_registry=channel_registry, retry_store=store)
    handler = RetrySweepHandler(actuator=actuator, retry_store=store)
    job = Job(
        job_id="j1", handler_name="retry_sweep", schedule="every 1m",
        idempotency_key="k1", last_run_at=None, next_run_at="", status="pending",
    )

    # 3. The scheduler sweep picks up the due row and retries it.
    result = await handler.execute(job)

    assert result.success is True
    # 4. Success edits the original Telegram message in place.
    adapter.edit_message.assert_awaited_once_with(555, 999, "Here's your plan...")

    remaining_due = await store.get_due()
    assert remaining_due == []  # row is now completed, no longer due
