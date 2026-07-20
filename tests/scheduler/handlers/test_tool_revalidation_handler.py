"""FX-09 — ToolRevalidationHandler: the scheduled wrapper around
revalidate_learned_tools that was previously reachable only via a manual CLI
command. The underlying quarantine logic itself is covered by
tests/tools/meta/test_tool_revalidation.py — these tests cover only the
JobHandler wrapping (name, success shape, failure containment).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.tool_revalidation_handler import ToolRevalidationHandler
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


def _job() -> Job:
    return Job(
        job_id="j1", handler_name="tool_revalidation", schedule="daily@06:00",
        idempotency_key="tool_revalidation:daily", last_run_at=None,
        next_run_at="2026-01-01T06:00:00+00:00", status="pending",
    )


async def test_handler_name() -> None:
    handler = ToolRevalidationHandler(db=object())  # type: ignore[arg-type]
    assert handler.handler_name == "tool_revalidation"


async def test_execute_success_reports_report_counts(tmp_db: DbPool) -> None:
    handler = ToolRevalidationHandler(db=tmp_db)
    # No learned_tools dir on disk in this sandboxed run -> revalidate_learned_tools
    # returns an empty report (its own documented "nothing to do" path), proving
    # the handler wraps it without raising and shapes a real JobResult.
    result = await handler.execute(_job())
    assert result.success is True
    assert result.error is None
    assert result.metadata["kept"] == 0
    assert result.metadata["evicted"] == []


async def test_execute_failure_is_never_raised_into_scheduler(tmp_db: DbPool) -> None:
    handler = ToolRevalidationHandler(db=tmp_db)
    with patch(
        "stackowl.scheduler.handlers.tool_revalidation_handler.revalidate_learned_tools",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await handler.execute(_job())
    assert result.success is False
    assert result.error == "boom"
    assert result.metadata == {"evicted": 0, "kept": 0}
