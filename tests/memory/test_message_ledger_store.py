"""Tests for MessageLedgerStore — universal per-message status lifecycle.

Runs against a real DbPool with migration 0089 applied (via the shared
tmp_db fixture in tests/conftest.py, which runs MigrationRunner), proving the
store against the actual message_ledger column set.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.message_ledger_store import MessageLedgerStore

pytestmark = pytest.mark.asyncio


async def test_insert_pending_then_get_pending_returns_row(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)

    await store.insert_pending(
        trace_id="trace-1", session_id="sess-1", channel="telegram",
        input_text="hello", chat_id=555,
    )

    pending = await store.get_pending()
    assert len(pending) == 1
    row = pending[0]
    assert row.trace_id == "trace-1"
    assert row.status == "pending"
    assert row.input_text == "hello"
    assert row.chat_id == "555"
    assert isinstance(row.chat_id, str)


async def test_insert_pending_is_idempotent_on_duplicate_trace_id(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)

    await store.insert_pending(
        trace_id="trace-dup", session_id="sess-1", channel="telegram", input_text="first",
    )
    await store.insert_pending(
        trace_id="trace-dup", session_id="sess-1", channel="telegram", input_text="second",
    )

    pending = await store.get_pending()
    assert len(pending) == 1
    assert pending[0].input_text == "first"  # first insert wins, second is a no-op


async def test_mark_completed_flips_status_and_excludes_from_pending(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-2", session_id="sess-1", channel="cli", input_text="hi",
    )

    won = await store.mark_completed("trace-2")

    assert won is True
    assert await store.get_pending() == []


async def test_mark_failed_flips_status_with_reason(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-3", session_id="sess-1", channel="cli", input_text="hi",
    )

    won = await store.mark_failed("trace-3", reason="ProviderTimeout")

    assert won is True
    assert await store.get_pending() == []


async def test_mark_absorbed_flips_status(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-4", session_id="sess-1", channel="telegram", input_text="steer",
    )

    won = await store.mark_absorbed("trace-4")

    assert won is True
    assert await store.get_pending() == []


async def test_flip_is_cas_guarded_second_call_is_noop(tmp_db: DbPool) -> None:
    """A redundant flip (e.g. the safety-net done-callback firing after
    persist_turn already flipped the row) must be a harmless no-op, not a
    double-write — this is what makes the two-writer pattern safe."""
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-5", session_id="sess-1", channel="cli", input_text="hi",
    )

    first = await store.mark_completed("trace-5")
    second = await store.mark_failed("trace-5", reason="should not apply")

    assert first is True
    assert second is False  # already completed — the failure flip loses the race


async def test_mark_completed_on_missing_row_returns_false(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    assert await store.mark_completed("no-such-trace") is False


async def test_mark_failed_with_long_reason_does_not_raise(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-6", session_id="sess-1", channel="cli", input_text="hi",
    )

    won = await store.mark_failed("trace-6", reason="x" * 5000)

    assert won is True


async def test_insert_pending_truncates_input_text_to_4000_chars(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-7", session_id="sess-1", channel="cli", input_text="x" * 9000,
    )

    pending = await store.get_pending()
    assert len(pending[0].input_text) == 4000


async def test_queries_are_owner_scoped(tmp_db: DbPool) -> None:
    store_a = MessageLedgerStore(tmp_db, owner_id="owner-a")
    store_b = MessageLedgerStore(tmp_db, owner_id="owner-b")

    await store_a.insert_pending(
        trace_id="trace-owner-a", session_id="sess-1", channel="cli", input_text="hi",
    )

    assert await store_b.get_pending() == []
    assert len(await store_a.get_pending()) == 1
    # A store scoped to a different owner cannot flip another owner's row.
    assert await store_b.mark_completed("trace-owner-a") is False


async def test_get_pending_rejects_non_positive_limit(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await store.get_pending(limit=0)
    with pytest.raises(ValueError, match="limit must be >= 1"):
        await store.get_pending(limit=-1)


async def test_chat_id_none_for_single_terminal_channel(tmp_db: DbPool) -> None:
    store = MessageLedgerStore(tmp_db)
    await store.insert_pending(
        trace_id="trace-cli", session_id="sess-1", channel="cli", input_text="hi",
    )

    row = (await store.get_pending())[0]
    assert row.chat_id is None
