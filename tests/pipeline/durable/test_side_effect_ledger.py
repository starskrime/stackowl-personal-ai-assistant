"""SideEffectLedger — the exactly-once intent->commit contract (Pass 3a).

THE key test: idempotency-key determinism, begin/commit/re-begin behaviour
(proceed -> commit -> already_committed without re-execute), the uncertain
path, the read/write gating helper, and cross-owner ledger isolation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.ledger import (
    SideEffectLedger,
    idempotency_key,
    is_side_effecting,
)

_ARGS = {"to": "a@b.com", "subject": "hi", "n": 3}


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "ledger.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


# ---- idempotency_key determinism ---------------------------------------------

def test_idempotency_key_deterministic() -> None:
    k1 = idempotency_key("t1", 0, "send_email", _ARGS)
    k2 = idempotency_key("t1", 0, "send_email", dict(reversed(list(_ARGS.items()))))
    assert k1 == k2  # arg dict ordering is irrelevant (canonical sorted JSON)


def test_idempotency_key_static_matches_free_function() -> None:
    assert SideEffectLedger.idempotency_key("t1", 0, "tool", _ARGS) == \
        idempotency_key("t1", 0, "tool", _ARGS)


def test_idempotency_key_changes_on_any_field() -> None:
    base = idempotency_key("t1", 0, "send_email", _ARGS)
    assert idempotency_key("t2", 0, "send_email", _ARGS) != base       # task
    assert idempotency_key("t1", 1, "send_email", _ARGS) != base       # step
    assert idempotency_key("t1", 0, "other_tool", _ARGS) != base       # tool
    assert idempotency_key("t1", 0, "send_email", {**_ARGS, "n": 4}) != base  # args


# ---- is_side_effecting gating -------------------------------------------------

def test_is_side_effecting_taxonomy() -> None:
    assert is_side_effecting("read") is False
    assert is_side_effecting("write") is True
    assert is_side_effecting("consequential") is True


def test_is_side_effecting_unknown_is_fail_safe() -> None:
    # An unknown severity must be treated as side-effecting (fail-safe: when
    # in doubt, guard). This prevents a misconfigured tool from silently
    # bypassing the exactly-once ledger.
    assert is_side_effecting("unknown_severity") is True


# ---- begin / commit / re-begin -----------------------------------------------

async def test_first_begin_proceeds_and_writes_intent(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    decision = await ledger.begin("t1", 0, "send_email", _ARGS)
    assert decision.outcome == "proceed"
    assert decision.result is None
    # an intent row now exists (owner-scoped storage key, looked up by task)
    rows = await pool.fetch_all(
        "SELECT status, result_blob, owner_id FROM side_effect_ledger "
        "WHERE task_id = ? AND step_index = ?",
        ("t1", 0),
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "intent"
    assert rows[0]["result_blob"] is None
    assert rows[0]["owner_id"] == "principal-alice"


async def test_commit_then_rebegin_returns_already_committed(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    first = await ledger.begin("t1", 0, "send_email", _ARGS)
    assert first.outcome == "proceed"

    await ledger.commit("t1", 0, "send_email", _ARGS, "message-id-42")

    # A second begin after commit must NOT re-execute — returns recorded result.
    second = await ledger.begin("t1", 0, "send_email", _ARGS)
    assert second.outcome == "already_committed"
    assert second.result == "message-id-42"


async def test_intent_without_commit_is_uncertain_on_rebegin(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    first = await ledger.begin("t1", 0, "send_email", _ARGS)
    assert first.outcome == "proceed"
    # No commit (simulating a crash mid-execution). Re-begin => uncertain.
    second = await ledger.begin("t1", 0, "send_email", _ARGS)
    assert second.outcome == "uncertain"
    assert second.result is None


async def test_cross_owner_ledger_isolation(pool: DbPool) -> None:
    alice = SideEffectLedger(pool, "principal-alice")
    bob = SideEffectLedger(pool, "principal-bob")

    a1 = await alice.begin("t1", 0, "send_email", _ARGS)
    await alice.commit("t1", 0, "send_email", _ARGS, "alice-result")
    assert a1.outcome == "proceed"

    # Same logical call for bob — alice's committed row is invisible, so bob
    # proceeds (gets to execute his own side effect exactly once).
    b1 = await bob.begin("t1", 0, "send_email", _ARGS)
    assert b1.outcome == "proceed"
    assert b1.result is None

    # And bob re-begin after his own commit sees only HIS result.
    await bob.commit("t1", 0, "send_email", _ARGS, "bob-result")
    b2 = await bob.begin("t1", 0, "send_email", _ARGS)
    assert b2.outcome == "already_committed"
    assert b2.result == "bob-result"
