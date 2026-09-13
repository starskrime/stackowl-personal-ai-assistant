"""One ended conversation, one summary — after its job row is deleted.

WHAT USED TO GUARANTEE IT. ``jobs.idempotency_key`` is UNIQUE and a rollover job's
key is ``rollover:{lane}:{ended_incarnation}``, so a boundary announced twice, or
announced and then recovered by the five-minute backstop, could only ever queue one
job. That held for as long as the row existed — and the row existed for ever,
because a finished one-shot was parked ``status='completed'``.

A finished one-shot is now DELETED (spec 2026-09-12), and with it the UNIQUE row.
What remains is the double-announce guard in the store and the lane's
``summary_enqueued_for`` marker, which ``conversation_sweep`` reads before it
recovers a boundary. The consumer writes that marker AFTER it enqueues — and when
that write fails, the job runs, its row is deleted, and the backstop five minutes
later finds a finalised lane with no marker and queues the same boundary again.

THE FIX IS AN ORDERING, not a new store. The handler writes its own incarnation's
marker BEFORE doing any work, and fails (so the job is retried and its row is kept)
when it cannot. The row — and the UNIQUE key it carries — therefore disappears only
after the marker is durable: one of the two guards always holds.

Real DbPool, real SessionStore, real scheduler, real consumer, real backstop. The AI
provider is the only thing faked.
"""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.rollover_summary_handler import (
    RolloverSummaryHandler,
    enqueue_rollover_summary,
    register_rollover_consumer,
)
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.conversation_sweep import ConversationSweepHandler
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.sessions import ChatType, ResetMode, ResetPolicy, SessionSource
from stackowl.sessions import store as store_module
from stackowl.sessions.store import SessionStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

UTC = datetime.UTC


class _Provider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages: Any, model: str = "", **_kw: Any) -> Any:
        self.prompts.append("\n".join(m.content for m in messages))

        class _R:
            content = json.dumps({"notable": True, "summary": "Agreed to ship it."})

        return _R()


class _Registry:
    def __init__(self, provider: _Provider) -> None:
        self._provider = provider

    def get_with_cascade(self, tier: str) -> tuple[_Provider, str]:
        return self._provider, "test-model"


class _MarkerWriteFails:
    """The consumer's store, at the moment its marker write raises."""

    async def mark_summary_enqueued(self, session_key: str, conversation_id: str) -> None:
        raise RuntimeError("database is locked")


class _Bus:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def subscribe(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def fire(self, event: str, payload: dict[str, Any]) -> None:
        for h in self.handlers.get(event, []):
            await h(payload)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


@pytest.fixture
async def db(tmp_path: Any) -> AsyncIterator[DbPool]:
    pool = DbPool(db_path=seed_schema(tmp_path / "test.db"))
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _a_boundary_the_sweeper_finalised(
    db: DbPool, tmp_path: Any
) -> tuple[SessionStore, str, str]:
    """The unattended 4 AM case: finalised WITHOUT minting, with a transcript."""
    store = SessionStore(db, ResetPolicy(mode=ResetMode.BOTH, at_hour=4), mirror_dir=tmp_path)
    source = SessionSource("Brain", "telegram", ChatType.DM, "123", identity_key="bakir")
    entry, _, _ = await store.resolve_for(source, datetime.datetime(2026, 7, 20, 22, tzinfo=UTC))
    await store.sweep(now=datetime.datetime(2026, 7, 21, 9, tzinfo=UTC))
    lane, ended = entry.session_key, entry.conversation_id

    stamp = datetime.datetime.now(UTC)
    await db.execute(
        "INSERT OR IGNORE INTO conversations (id, session_key, owl_name, started_at, "
        "message_count) VALUES (?, ?, 'Brain', ?, 2)",
        (ended, lane, stamp.isoformat()),
    )
    for i, (role, text) in enumerate((("user", "ship it?"), ("assistant", "shipping"))):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at, "
            "trace_id) VALUES (?, ?, ?, ?, ?, '')",
            (str(uuid.uuid4()), ended, role, text,
             (stamp + datetime.timedelta(seconds=i)).isoformat()),
        )
    return store, lane, ended


async def _announce(db: DbPool, lane: str, ended: str) -> None:
    """The live event, reaching a consumer whose marker write fails."""
    bus = _Bus()
    register_rollover_consumer(bus, db, store=_MarkerWriteFails())
    await bus.fire(SessionStore.ROLLOVER_EVENT, {
        "session_key": lane, "old_conversation_id": ended, "new_conversation_id": None,
        "reason": "idle", "owl_name": "Brain", "channel": "telegram",
        "identity_key": "bakir", "message_count": 2, "completed_turns": 1,
    })


async def _backstop(db: DbPool, store: SessionStore) -> None:
    sweep = ConversationSweepHandler(store, enqueue_summary=enqueue_rollover_summary, db=db)
    await sweep.execute(Job(
        job_id="sweep", handler_name="conversation_sweep", schedule="every 5m",
        idempotency_key="sweep", last_run_at=None, next_run_at="", status="pending",
    ))


def _scheduler(db: DbPool, provider: _Provider) -> JobScheduler:
    reg = HandlerRegistry.instance()
    reg.register(RolloverSummaryHandler(
        db=db, bridge=SqliteMemoryBridge(db), provider_registry=_Registry(provider),
    ))
    return JobScheduler(db=db, handler_registry=reg)


async def _rollover_jobs(db: DbPool) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT job_id, status FROM jobs WHERE handler_name = 'rollover_summary'"
    )


async def test_a_failed_marker_write_does_not_summarise_the_boundary_twice(
    db: DbPool, tmp_path: Any
) -> None:
    store, lane, ended = await _a_boundary_the_sweeper_finalised(db, tmp_path)
    provider = _Provider()
    sched = _scheduler(db, provider)

    await _announce(db, lane, ended)
    assert len(await _rollover_jobs(db)) == 1
    assert [e.session_key for e in await store.lanes_awaiting_summary()] == [lane], (
        "precondition: the consumer's marker write failed, so the lane looks unsummarised"
    )

    await sched._poll()
    assert provider.prompts and len(provider.prompts) == 1
    assert await _rollover_jobs(db) == [], "the finished one-shot must be deleted"

    await _backstop(db, store)  # five minutes later
    await sched._poll()

    assert len(provider.prompts) == 1, (
        "the boundary was summarised twice — the UNIQUE row that used to absorb the "
        "backstop's re-enqueue is gone, and nothing else stopped it"
    )
    assert await _rollover_jobs(db) == []


async def test_a_job_that_cannot_write_its_marker_keeps_its_row_and_its_key(
    db: DbPool, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the ordering. No work happens before the marker is durable,
    and the row that carries the UNIQUE key is kept (retried), so the backstop is
    still refused — and once the marker CAN be written, exactly one summary results.

    Only the JOB's marker write is failed: the helper is told which namespace called
    it, and the job logs as memory while the store (the backstop's write) logs as
    gateway. The consumer's own write fails separately, through its store."""
    store, lane, ended = await _a_boundary_the_sweeper_finalised(db, tmp_path)
    provider = _Provider()
    sched = _scheduler(db, provider)
    real = store_module.record_summary_enqueued
    job_marker_down = [True]

    async def _job_marker_fails_while_down(
        pool: DbPool, session_key: str, conversation_id: str, *, logger: Any,
    ) -> int:
        if logger is log.memory and job_marker_down[0]:
            raise RuntimeError("database is locked")
        return await real(pool, session_key, conversation_id, logger=logger)

    monkeypatch.setattr(store_module, "record_summary_enqueued", _job_marker_fails_while_down)

    await _announce(db, lane, ended)
    await sched._poll()

    assert provider.prompts == [], "a summary was produced before its marker was durable"
    jobs = await _rollover_jobs(db)
    assert len(jobs) == 1 and jobs[0]["status"] == "pending", (
        "the job must be kept for a retry, not deleted as finished"
    )
    await _backstop(db, store)
    assert len(await _rollover_jobs(db)) == 1, "the backstop queued the boundary again"

    job_marker_down[0] = False  # the database recovers; the retry comes due
    await db.execute(
        "UPDATE jobs SET retry_at = ? WHERE handler_name = 'rollover_summary'",
        ((datetime.datetime.now(UTC) - datetime.timedelta(minutes=1)).isoformat(),),
    )
    await sched._poll()
    assert len(provider.prompts) == 1
    assert await _rollover_jobs(db) == []

    await _backstop(db, store)
    await sched._poll()

    assert len(provider.prompts) == 1, "the boundary was summarised twice after the retry"
