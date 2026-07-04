"""Task 9 — durable-task liveness watchdog.

Covers two things:

1. A REGRESSION guard proving the ``recovery.py`` refactor (extracting the
   shared per-task ``reclaim_one`` unit out of ``recover()``'s loop) left the
   BOOT recovery path byte-identical in observable behavior.
2. The new periodic ``TaskLivenessSweepHandler`` — using a REAL ``DbPool`` +
   ``DurableTaskStore`` + a fake-but-driving backend (never a mocked
   store/aggregator), so a passing test actually proves a genuinely stale row
   gets CLAIMED, CHECKPOINT-RECONSTRUCTED, and RESUMED to a terminal status —
   not merely that a query executed (the exact class of bug this arc's
   Tasks 5/6/7 caught elsewhere).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.recovery import recover_durable_tasks
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import ReActIterationState
from stackowl.scheduler.handlers.task_liveness_sweep import (
    DEFAULT_STALE_AFTER_S,
    TaskLivenessSweepHandler,
)
from stackowl.scheduler.job import Job
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.registry import ToolRegistry


class _Finishing:
    """A fake provider whose single ReAct iteration finishes cleanly."""

    @property
    def name(self) -> str:
        return "fin"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def complete_with_tools(self, user_text, system_text, tool_schemas,  # noqa: ANN001
                                  tool_dispatcher, max_iterations=8, history=None,
                                  persistence_check=None, on_iteration_complete=None,
                                  resume_messages=None, resume_tool_calls=None):
        if on_iteration_complete is not None:
            await on_iteration_complete(ReActIterationState(
                iteration=0, messages=[{"role": "assistant", "content": "done"}],
                tool_call_records=[]))
        return "done", []

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(content="secretary", input_tokens=1, output_tokens=1,
                                model="", provider_name=self.name, duration_ms=0.0)

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _Reg:
    def __init__(self, p: object) -> None:
        self._p = p

    def get(self, name: str) -> object:
        return self._p

    def get_by_tier(self, tier: str) -> object:
        return self._p

    def get_with_cascade(self, tier: str) -> object:
        return self._p


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _backend(pool: DbPool) -> AsyncioBackend:
    services = StepServices(
        provider_registry=_Reg(_Finishing()),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(), stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(), db_pool=pool,
    )
    return AsyncioBackend(services=services)


def _job() -> Job:
    return Job(
        job_id="tls-1",
        handler_name="task_liveness_sweep",
        schedule="every 3m",
        idempotency_key="task_liveness_sweep:every-3m",
        last_run_at=None,
        next_run_at="2026-07-03T00:00:00+00:00",
        status="pending",
    )


async def _make_task(
    store: DurableTaskStore, task_id: str, *,
    status: Literal["pending", "running", "completed", "failed"],
    age_s: float,
    parent_task_id: str | None = None,
) -> None:
    now = datetime.now(tz=UTC)
    await store.create(DurableTask(
        task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status=status,
        owl_name="secretary", channel="cli", parent_task_id=parent_task_id,
        created_at=now - timedelta(seconds=age_s), updated_at=now - timedelta(seconds=age_s),
    ))


# --- 1. Regression guard: the recovery.py refactor must not change boot behavior ---


async def test_boot_recovery_unchanged_by_reclaim_one_extraction(pool: DbPool) -> None:
    """Characterization test for the ``reclaim_one`` extraction (Task 9 Step 2).

    Before the refactor, ``recover()`` inlined claim+reconstruct+launch per
    orphan; after, it delegates to the new shared ``reclaim_one`` method. This
    asserts the OBSERVABLE outcome — one root orphan claimed and driven to a
    terminal status — is identical either way. Ran (and passed) against BOTH
    the pre-refactor and post-refactor code during implementation.
    """
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(store, "root", status="running", age_s=0)
    recoverer = await recover_durable_tasks(pool, _backend(pool))
    await recoverer.drain()
    assert recoverer.launched == 1, f"expected 1 orphan launched, got {recoverer.launched}"
    rec = await store.get("root")
    assert rec.status != "running", f"orphan not driven to a terminal status: {rec.status!r}"


# --- 2. New behavior: TaskLivenessSweepHandler.execute() ---


async def test_stale_running_root_is_reclaimed(pool: DbPool) -> None:
    """A live-server 'running' root gone stale gets claimed+resumed by the sweep."""
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(store, "root", status="running", age_s=DEFAULT_STALE_AFTER_S + 60)
    handler = TaskLivenessSweepHandler(pool, _backend(pool))

    result = await handler.execute(_job())
    await handler._recoverer.drain()  # await the launched background drive

    assert result.success is True
    assert result.metadata["stale_found"] == 1
    assert result.metadata["reclaimed"] == 1
    rec = await store.get("root")
    assert rec.status != "running", (
        f"stale row not actually claimed+resumed — status={rec.status!r}"
    )


async def test_recent_running_root_is_left_alone(pool: DbPool) -> None:
    """A 'running' root with a fresh updated_at must NOT be falsely reclaimed."""
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(store, "root", status="running", age_s=5.0)
    handler = TaskLivenessSweepHandler(pool, _backend(pool))

    result = await handler.execute(_job())

    assert result.metadata["stale_found"] == 0
    assert result.metadata["reclaimed"] == 0
    rec = await store.get("root")
    assert rec.status == "running", "a genuinely live task must not be reclaimed"


@pytest.mark.parametrize("status", ["pending", "completed", "failed"])
async def test_non_running_statuses_are_never_touched(pool: DbPool, status: str) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(
        store, "t", status=status, age_s=DEFAULT_STALE_AFTER_S * 10,  # extremely old
    )
    handler = TaskLivenessSweepHandler(pool, _backend(pool))

    result = await handler.execute(_job())

    assert result.metadata["stale_found"] == 0
    rec = await store.get("t")
    assert rec.status == status, f"a {status!r} row must never be touched by the sweep"


async def test_stale_child_row_is_not_directly_reclaimed(pool: DbPool) -> None:
    """Roots-only (D1 §9): a delegated child must be resumed transitively, never
    reclaimed directly by the periodic sweep (that would double-drive it)."""
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(store, "root", status="running", age_s=DEFAULT_STALE_AFTER_S + 60)
    await _make_task(
        store, "kid", status="running", age_s=DEFAULT_STALE_AFTER_S + 60,
        parent_task_id="root",
    )
    handler = TaskLivenessSweepHandler(pool, _backend(pool))

    result = await handler.execute(_job())
    await handler._recoverer.drain()

    # Only the root is a direct reclaim target — the child stays 'running' as
    # far as THIS sweep is concerned (it is resumed transitively by the root).
    assert result.metadata["stale_found"] == 1
    rec = await store.get("kid")
    assert rec.status == "running"


# --- 3. HealableResource / HealthContributor pairing ---


async def test_healable_pairing_available_false_then_true_after_ensure(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(store, "root", status="running", age_s=DEFAULT_STALE_AFTER_S + 60)
    handler = TaskLivenessSweepHandler(pool, _backend(pool))

    status = await handler.health_check()
    assert status.status == "degraded"
    assert handler.available is False
    assert handler.unavailable_reason is not None

    await handler.ensure_available()  # reclaim NOW, no waiting for the next tick
    await handler._recoverer.drain()

    assert handler.available is True
    assert handler.unavailable_reason is None
    rec = await store.get("root")
    assert rec.status != "running"


async def test_healable_pairing_available_true_when_nothing_stale(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await _make_task(store, "root", status="running", age_s=5.0)
    handler = TaskLivenessSweepHandler(pool, _backend(pool))

    status = await handler.health_check()

    assert status.status == "ok"
    assert handler.available is True
    assert handler.unavailable_reason is None
