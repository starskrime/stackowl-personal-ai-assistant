"""WS-B merge-gate journey — a user-scheduled GOAL delivers its answer back home.

The headline P0: a user schedules a natural-language goal from a chat (e.g. a
Telegram chat ``12345``); later, on a fresh scheduler tick with NO live session,
the goal's produced answer must reach the SAME chat it was scheduled from — never
silently dropped.

This drives the WHOLE chain end-to-end, mocking ONLY the AI provider and
asserting the USER OUTCOME (a message delivered to the originating chat), not a
tool's return shape:

1. CREATE (real path) — the genuine :class:`CronjobTool` (``action="create"``)
   runs inside a ``TraceContext.start(channel="telegram", reply_target=12345)``,
   exactly as a real Telegram-channel turn would. We assert the persisted ``jobs``
   row carries ``target_addresses={"telegram": 12345}`` (the durable-capture half).

2. FIRE (real path) — the persisted job is dispatched through the REAL
   ``JobScheduler.run_now`` → REAL :class:`GoalExecutionHandler` wired with a REAL
   :class:`ProactiveJobDeliverer` over a capturing :class:`ProactiveDeliverer` +
   REAL :class:`DeliveryLedger` (the morning_brief wiring). The AI provider is the
   ONLY mock — a stub backend returning a deterministic answer.

3. OUTCOME — the capturing deliverer received EXACTLY ONE send, addressed to chat
   ``12345``, carrying the produced answer; ``job_results`` records ``completed``.

4. HONESTY (second case) — a goal job with NO resolvable target (no
   ``reply_target``, empty owner allowlist) fires → NO send AND ``job_results``
   records ``undeliverable`` (never ``completed``), the answer preserved in
   ``result_text``.

REAL: the migrated :class:`DbPool`, the :class:`CronjobTool`, ``TraceContext``,
the :class:`JobScheduler` + ``jobs``/``job_results`` tables, the
:class:`GoalExecutionHandler`, the :class:`ProactiveJobDeliverer`,
:class:`ProactiveDeliverer`, :class:`NotificationRouter`, the
:class:`DeliveryLedger`. FAKED: ONLY the AI provider (a stub backend) and the
channel transport (a recording adapter — the universal journey seam).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.delivery_ledger import DeliveryLedger
from stackowl.notifications.proactive_job import ProactiveJobDeliverer
from stackowl.notifications.router import NotificationRouter
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.scheduler.scheduler_helpers import row_to_job
from stackowl.tools.base import ToolResult
from stackowl.tools.scheduling.cronjob import CronjobTool

pytestmark = pytest.mark.asyncio

_CHAT_ID = 12345
_GOAL = "What's the weather?"
_ANSWER = "the weather is sunny"


# --- the channel transport seam (the ONLY non-AI mock) --------------------------


class _RecordingTelegramAdapter:
    """A telegram-like adapter: ``send_text`` accepts an explicit ``chat_id``.

    Models a FRESH scheduler process — ``_last_chat_id`` is ``None``, so a
    target-less send reaches nobody. The durable target captured at create time
    must be threaded through to ``chat_id`` for the answer to arrive.
    """

    def __init__(self, name: str = "telegram") -> None:
        self._name = name
        self._last_chat_id: int | None = None
        self.sends: list[tuple[str, Any]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str, *, chat_id: str | int | None = None) -> None:
        if chat_id is None and self._last_chat_id is None:
            raise RuntimeError("no chat target (fresh process, _last_chat_id is None)")
        self.sends.append((text, chat_id if chat_id is not None else self._last_chat_id))


class _StubBackend:
    """The ONLY AI mock — stands in for the whole pipeline/LLM.

    Records every PipelineState it runs and returns the deterministic answer as a
    single final response chunk (the goal handler joins ``responses`` into the
    body it delivers).
    """

    def __init__(self, answer: str = _ANSWER) -> None:
        self.calls: list[PipelineState] = []

    async def run(self, state: PipelineState) -> PipelineState:
        self.calls.append(state)
        chunk = ResponseChunk(
            content=_ANSWER,
            is_final=True,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,))

    async def shutdown(self) -> None:
        return None


# --- settings (model_copy over the YAML-source-loaded base) ---------------------


def _settings() -> Settings:
    """Build settings WITHOUT a resolvable telegram owner.

    ``Settings(...)`` loads ``~/.stackowl/stackowl.yaml`` via a source that
    outranks constructor kwargs, so a real machine's allowlist could leak in and
    make the "no resolvable target" case spuriously deliverable. ``model_copy``
    forces a known-empty notification/brief surface AFTER construction. There is
    NO telegram allowlist here, so the owner fallback resolves nothing — the
    durable target must come from the request ``reply_target`` (case 1) or be
    genuinely unresolvable (case 2).
    """
    return Settings().model_copy(
        update={
            "notifications": NotificationSettings(),
            "brief": BriefSettings(channels=["telegram"]),
            "system": SystemSettings(timezone="UTC"),
        }
    )


# --- fixtures -------------------------------------------------------------------


@pytest.fixture()
async def migrated_db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "sched_goal.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _clean() -> AsyncIterator[None]:  # type: ignore[misc]
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()
    TestModeGuard.deactivate()
    yield
    HandlerRegistry.reset()
    ChannelRegistry.instance().reset()
    TestModeGuard.deactivate()


@pytest.fixture(autouse=True)
def _relax_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # The goal handler + scheduler refuse to run under TEST_MODE; this journey
    # drives the REAL dispatch path.
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


# --- helpers --------------------------------------------------------------------


async def _seed_session(db: DbPool, session_id: str, owl: str = "secretary") -> None:
    """A ``conversations`` row so CronjobTool's ``resolve_owl`` finds the owner."""
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, session_id, owl, datetime.now(UTC).isoformat(), 0),
    )


async def _create_goal_via_tool(
    db: DbPool,
    *,
    session_id: str,
    channel: str | None,
    reply_target: str | int | None,
) -> ToolResult:
    """Run the REAL CronjobTool create inside a real TraceContext (the create half).

    This is exactly the production path: a tool call on a turn whose
    TraceContext carries the originating ``channel`` + ``reply_target``. The tool
    captures that into the job's durable ``target_*`` columns.
    """
    settings = _settings()
    token = set_services(StepServices(db_pool=db, settings=settings))
    ttoken = TraceContext.start(
        session_id=session_id,
        interactive=True,
        channel=channel,
        reply_target=reply_target,
    )
    try:
        return await CronjobTool().execute(
            action="create", prompt=_GOAL, schedule="daily@09:00"
        )
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)


def _wire_goal_handler(
    db: DbPool, adapter: _RecordingTelegramAdapter
) -> tuple[JobScheduler, _StubBackend]:
    """Register the REAL GoalExecutionHandler over the morning_brief delivery wiring.

    Real ProactiveJobDeliverer ⟶ real ProactiveDeliverer ⟶ capturing adapter, plus
    a real DeliveryLedger. ONLY the backend (AI) is stubbed.
    """
    settings = _settings()
    ChannelRegistry.instance().register(cast(Any, adapter))
    router = NotificationRouter(db=db, settings=settings)
    deliverer = ProactiveDeliverer(
        router=router, registry=ChannelRegistry.instance(), settings=settings
    )
    ledger = DeliveryLedger(db=db)
    job_deliverer = ProactiveJobDeliverer(deliverer=deliverer, ledger=ledger)

    backend = _StubBackend()
    handler = GoalExecutionHandler(
        backend=cast(Any, backend),
        db=db,
        settings=settings,
        job_deliverer=job_deliverer,
    )
    HandlerRegistry.instance().register(handler)
    scheduler = JobScheduler(db=db)
    return scheduler, backend


async def _job_by_id(db: DbPool, job_id: str) -> Job | None:
    """Read a persisted job back through the real ``row_to_job`` decoder.

    Empty ``target_*`` columns serialize to NULL in the row; ``row_to_job``
    decodes them to ``[]`` / ``{}`` (exactly what the scheduler sees), so this is
    the production-accurate way to assert the durable capture.
    """
    rows = await db.fetch_all("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    return row_to_job(rows[0]) if rows else None


async def _latest_job_result(db: DbPool, job_id: str) -> dict[str, Any] | None:
    rows = await db.fetch_all(
        "SELECT status, result_text FROM job_results WHERE job_id = ? "
        "ORDER BY run_at DESC LIMIT 1",
        (job_id,),
    )
    return rows[0] if rows else None


# --- CASE 1: the headline win — scheduled goal delivers home --------------------


async def test_scheduled_goal_delivers_answer_to_originating_chat(
    migrated_db: DbPool,
) -> None:
    """End-to-end: create from telegram chat 12345 → fire → answer reaches 12345."""
    session_id = "tg:session-1"
    await _seed_session(migrated_db, session_id)

    # --- CREATE HALF (real CronjobTool + real TraceContext) ---
    result = await _create_goal_via_tool(
        migrated_db, session_id=session_id, channel="telegram", reply_target=_CHAT_ID
    )
    assert result.success, f"create failed: {result.error!r}"
    body = json.loads(result.output)
    assert body["created"] is True
    job_id = body["job_id"]

    # The durable-capture half: the persisted job carries the originating chat.
    # Read it back through the real Job decoder (row_to_job), as the scheduler does.
    persisted = await _job_by_id(migrated_db, job_id)
    assert persisted is not None, "the goal job was not persisted"
    assert persisted.target_channels == ["telegram"]
    assert persisted.target_addresses == {"telegram": _CHAT_ID}

    # --- FIRE HALF (real scheduler dispatch, AI stubbed) ---
    adapter = _RecordingTelegramAdapter()
    scheduler, backend = _wire_goal_handler(migrated_db, adapter)

    job_result = await scheduler.run_now(job_id)

    # OUTCOME — the user actually received the answer, exactly once, at chat 12345.
    assert len(adapter.sends) == 1, "the answer must reach the durable target exactly once"
    text, chat_id = adapter.sends[0]
    assert chat_id == _CHAT_ID, "delivered to the chat the goal was scheduled from"
    assert text == _ANSWER, "the produced answer is transported verbatim"

    # The goal genuinely ran (the backend produced the answer for THIS goal).
    assert len(backend.calls) == 1
    assert backend.calls[0].input_text == _GOAL
    assert backend.calls[0].defer_delivery is True, "the handler owns delivery (no double-send)"

    # The recorded status is the honest 'completed'.
    assert job_result is not None and job_result.success is True
    rollup = await _latest_job_result(migrated_db, job_id)
    assert rollup is not None and rollup["status"] == "completed"
    assert rollup["result_text"] == _ANSWER


# --- CASE 2: honesty invariant — no target → no send, never 'completed' ---------


async def test_scheduled_goal_with_no_target_is_undeliverable_not_completed(
    migrated_db: DbPool,
) -> None:
    """A goal with no resolvable recipient never sends and records 'undeliverable'.

    The goal is created from a non-chat channel (``cli``) with NO ``reply_target``
    and NO telegram owner allowlist, so the durable-target capture resolves
    nothing. When fired, the answer is still produced and preserved in
    ``result_text`` — but NOTHING is sent and the status is the honest
    ``undeliverable`` (never a dressed-up ``completed``).
    """
    session_id = "cli:session-1"
    await _seed_session(migrated_db, session_id)

    # CREATE from a target-less surface: cli channel, no reply_target.
    result = await _create_goal_via_tool(
        migrated_db, session_id=session_id, channel="cli", reply_target=None
    )
    assert result.success, f"create failed: {result.error!r}"
    body = json.loads(result.output)
    job_id = body["job_id"]
    # The tool is honest at create time: created, but unreachable on this channel.
    assert body.get("created_but_unreachable") is True

    persisted = await _job_by_id(migrated_db, job_id)
    assert persisted is not None
    assert persisted.target_channels == [], "no durable target captured"
    assert persisted.target_addresses == {}

    # FIRE — same real wiring; the answer is produced but there's nobody to send to.
    adapter = _RecordingTelegramAdapter()
    scheduler, backend = _wire_goal_handler(migrated_db, adapter)

    job_result = await scheduler.run_now(job_id)

    # OUTCOME — nothing was sent, and the status is honestly 'undeliverable'.
    assert adapter.sends == [], "no send when the recipient is unresolvable"
    assert len(backend.calls) == 1, "the goal still ran (answer produced)"
    rollup = await _latest_job_result(migrated_db, job_id)
    assert rollup is not None
    assert rollup["status"] == "undeliverable", "never a fake 'completed'"
    assert rollup["status"] != "completed"
    # The answer is never lost — it's preserved for /agents log.
    assert rollup["result_text"] == _ANSWER
    assert job_result is not None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
