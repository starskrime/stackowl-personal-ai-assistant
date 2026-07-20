"""recover_pending_messages — boot recovery for orphaned message_ledger rows."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.message_ledger_store import MessageLedgerStore
from stackowl.pipeline.durable.recovery import recover_pending_messages

pytestmark = pytest.mark.asyncio


class _FakeBackend:
    """Simulates a redrive that reaches persist_turn and flips the row."""

    def __init__(self, store: MessageLedgerStore) -> None:
        self._store = store
        self.ran_states: list[object] = []

    async def run(self, state):  # noqa: ANN001, ANN201 — test double
        self.ran_states.append(state)
        await self._store.mark_completed(state.trace_id)
        return state


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "msg-recover.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_recover_pending_messages_redrives_and_flips_row(pool: DbPool) -> None:
    """A row left 'pending' by a crashed process is redriven at boot and
    ends up flipped to a terminal status — the crash-recovery round trip."""
    store = MessageLedgerStore(pool)
    await store.insert_pending(
        trace_id="crash-trace", session_id="crash-sess", channel="telegram",
        input_text="were you working on this?", chat_id=42,
    )

    backend = _FakeBackend(store)
    recoverer = await recover_pending_messages(pool, backend)
    await recoverer.drain()

    assert recoverer.launched == 1
    assert len(backend.ran_states) == 1
    reconstructed = backend.ran_states[0]
    assert reconstructed.trace_id == "crash-trace"
    assert reconstructed.session_id == "crash-sess"
    assert reconstructed.input_text == "were you working on this?"
    assert reconstructed.reply_target == "42"  # chat_id threaded back for delivery
    assert await store.get_pending() == []


async def test_recover_pending_messages_is_noop_when_nothing_pending(pool: DbPool) -> None:
    backend = _FakeBackend(MessageLedgerStore(pool))

    recoverer = await recover_pending_messages(pool, backend)
    await recoverer.drain()

    assert recoverer.launched == 0
    assert backend.ran_states == []


async def test_recover_pending_messages_ignores_non_pending_rows(pool: DbPool) -> None:
    store = MessageLedgerStore(pool)
    await store.insert_pending(
        trace_id="already-done", session_id="sess-1", channel="cli", input_text="hi",
    )
    await store.mark_completed("already-done")

    backend = _FakeBackend(store)
    recoverer = await recover_pending_messages(pool, backend)
    await recoverer.drain()

    assert recoverer.launched == 0
    assert backend.ran_states == []
