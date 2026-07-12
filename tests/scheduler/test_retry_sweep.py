from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.memory.retry_queue_store import RetryQueueRow
from stackowl.pipeline.retry_actuator import RetryOutcome
from stackowl.scheduler.handlers.retry_sweep import RetrySweepHandler
from stackowl.scheduler.job import Job


def _row(id_="r1"):
    return RetryQueueRow(
        id=id_, trace_id="t1", session_id="s1", goal="g", banned_capabilities=[],
        attempt_count=0, status="pending", next_retry_at="", last_error=None,
        channel="telegram", channel_chat_id="1", channel_message_id="2",
        created_at="", updated_at="",
    )


@pytest.mark.asyncio
async def test_sweep_retries_all_due_rows():
    retry_store = MagicMock()
    retry_store.get_due = AsyncMock(return_value=[_row("r1"), _row("r2")])

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock(return_value=RetryOutcome(status="completed"))

    handler = RetrySweepHandler(actuator=actuator, retry_store=retry_store)
    job = Job(
        job_id="j1", handler_name="retry_sweep", schedule="every 1m",
        idempotency_key="k1", last_run_at=None, next_run_at="", status="pending",
    )

    result = await handler.execute(job)

    assert result.success is True
    assert actuator.attempt_retry.await_count == 2


@pytest.mark.asyncio
async def test_sweep_never_raises_on_actuator_failure():
    retry_store = MagicMock()
    retry_store.get_due = AsyncMock(return_value=[_row("r1")])

    actuator = MagicMock()
    actuator.attempt_retry = AsyncMock(side_effect=RuntimeError("boom"))

    handler = RetrySweepHandler(actuator=actuator, retry_store=retry_store)
    job = Job(
        job_id="j1", handler_name="retry_sweep", schedule="every 1m",
        idempotency_key="k1", last_run_at=None, next_run_at="", status="pending",
    )

    result = await handler.execute(job)  # must not raise
    assert result.success is True  # sweep itself succeeded even if one row's retry errored
