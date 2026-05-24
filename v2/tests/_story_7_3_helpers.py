"""Shared test helpers for Story 7.3 — used by test_story_7_3 and test_story_7_3b.

Kept in a non-``test_`` module so pytest doesn't try to collect tests here
and so neither test file is forced over the B2 300-line cap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import pytest

from stackowl.brief.assemblers import BriefContext
from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.events.bus import EventBus
from stackowl.memory.bridge import NullMemoryBridge
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.pipeline.state import PipelineState
from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
from stackowl.scheduler.job import Job


class StubDb:
    """Minimal DbPool stand-in — records executes + serves canned fetches."""

    def __init__(
        self, fetch_responses: dict[str, list[dict[str, Any]]] | None = None
    ) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self._responses = fetch_responses or {}

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executes.append((sql, tuple(params)))

    async def fetch_all(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        for needle, rows in self._responses.items():
            if needle in sql:
                return list(rows)
        return []


class StubMemory(NullMemoryBridge):
    """NullMemoryBridge that lets tests inject recall + staged data."""

    def __init__(
        self,
        *,
        records: list[MemoryRecord] | None = None,
        staged: list[StagedFact] | None = None,
        recall_exc: Exception | None = None,
    ) -> None:
        self._records = records or []
        self._staged = staged or []
        self._recall_exc = recall_exc
        self.recall_calls: list[tuple[str, int]] = []

    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        self.recall_calls.append((query, limit))
        if self._recall_exc is not None:
            raise self._recall_exc
        return list(self._records[:limit])

    async def list_staged(
        self,
        status: Literal["staged", "committed", "rejected"] = "staged",
    ) -> list[StagedFact]:
        return [f for f in self._staged if f.status == status]


class StubScheduler:
    """Minimal scheduler — only :meth:`list_jobs` is exercised."""

    def __init__(self, jobs: list[Job] | None = None) -> None:
        self._jobs = jobs or []

    async def list_jobs(self) -> list[Job]:
        return list(self._jobs)


def make_record(content: str) -> MemoryRecord:
    return MemoryRecord(
        fact_id=str(uuid.uuid4()),
        content=content,
        embedding=[0.0, 0.0],
        embedding_model="stub",
        committed_at=datetime.now(UTC),
        source_type="conversation",
        source_ref="test",
    )


def make_staged(status: Literal["staged", "committed", "rejected"] = "staged") -> StagedFact:
    return StagedFact(
        content="fact",
        source_type="conversation",
        source_ref="test",
        confidence=0.9,
        status=status,
    )


def make_job(handler: str = "morning_brief", **overrides: Any) -> Job:
    defaults: dict[str, Any] = dict(
        job_id=f"job-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@08:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )
    defaults.update(overrides)
    return Job(**defaults)


def disable_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


def make_settings(
    sections: dict[str, bool] | None = None, tz: str = "UTC"
) -> Settings:
    return Settings(
        brief=BriefSettings(sections=sections or {}),
        system=SystemSettings(timezone=tz),
    )


def make_ctx(settings: Settings | None = None, job_id: str = "job-x") -> BriefContext:
    return BriefContext(
        job_id=job_id,
        last_brief_time=None,
        settings=settings or make_settings(),
    )


def make_state() -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_id="sess-1",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


def make_handler(
    *,
    db: StubDb | None = None,
    mem: StubMemory | None = None,
    sched: StubScheduler | None = None,
    bus: EventBus | None = None,
    settings: Settings | None = None,
) -> MorningBriefHandler:
    return MorningBriefHandler(
        memory_bridge=mem or StubMemory(),
        scheduler=sched or StubScheduler(),  # type: ignore[arg-type]
        db=db or StubDb(),  # type: ignore[arg-type]
        event_bus=bus or EventBus(),
        settings=settings or make_settings(),
    )
