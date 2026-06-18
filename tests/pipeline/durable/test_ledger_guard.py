"""ledger_guard — the dormant-by-default exactly-once dispatch seam (S2).

Drives the REAL :class:`SideEffectLedger` over a real SQLite DB (DbPool +
MigrationRunner, no mocks).  Proves:

* DORMANT: with no active durable context the guard runs execute_fn once and
  returns its result, and writes NOTHING to the ledger.
* a read/pure tool under an ACTIVE context is NOT ledger-guarded.
* side-effecting + active: first call proceeds (runs + commits); a second
  guard with the same (task, iteration, tool, args) returns the recorded result
  WITHOUT re-executing (exactly-once).
* an intent-without-commit yields ``uncertain`` → :class:`DurableReplayUncertain`.
* the contextvars activate/get_active set+reset correctly (async-safe).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.exceptions import DurableReplayUncertain
from stackowl.pipeline.durable.context import (
    DurableReActContext,
    activate,
    get_active,
)
from stackowl.pipeline.durable.ledger import SideEffectLedger
from stackowl.pipeline.durable.ledger_guard import ledger_guard
from stackowl.tools.base import ToolResult

_ARGS: dict[str, object] = {"path": "data/x", "content": "hi"}


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "guard.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _ok(output: str) -> ToolResult:
    return ToolResult(success=True, output=output, error=None, duration_ms=1.0)


class _Counter:
    """A zero-arg async tool-call double that counts invocations."""

    def __init__(self, output: str = "ran") -> None:
        self.calls = 0
        self._output = output

    async def __call__(self) -> ToolResult:
        self.calls += 1
        return _ok(self._output)


async def _ledger_rows(pool: DbPool) -> list[dict[str, object]]:
    return await pool.fetch_all(
        "SELECT idempotency_key, status, result_blob, tool_name FROM side_effect_ledger",
        (),
    )


# ---- DORMANT (no active context) ---------------------------------------------

async def test_dormant_no_context_runs_once_and_no_ledger(pool: DbPool) -> None:
    fn = _Counter("dormant-output")
    # No active durable context.
    assert get_active() is None
    result = await ledger_guard("write_file", _ARGS, "write", fn)
    assert result.output == "dormant-output"
    assert fn.calls == 1  # execute_fn ran exactly once
    # The ledger table stays empty — nothing was guarded.
    assert await _ledger_rows(pool) == []


# ---- read/pure tool under an ACTIVE context ----------------------------------

async def test_read_tool_under_active_context_not_guarded(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx = DurableReActContext(task_id="t-read", owner_id="principal-alice", ledger=ledger)
    fn = _Counter("read-output")
    with activate(ctx):
        result = await ledger_guard("read_file", _ARGS, "read", fn)
    assert result.output == "read-output"
    assert fn.calls == 1  # executed
    assert await _ledger_rows(pool) == []  # read tools are never ledgered


# ---- side-effecting + active context: exactly-once ---------------------------

async def test_side_effecting_first_proceeds_then_replays_exactly_once(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx = DurableReActContext(task_id="t-se", owner_id="principal-alice", ledger=ledger)

    fn1 = _Counter("effect-output")
    with activate(ctx):
        r1 = await ledger_guard("send_email", _ARGS, "write", fn1)
    assert r1.output == "effect-output"
    assert fn1.calls == 1  # ran once

    rows = await _ledger_rows(pool)
    assert len(rows) == 1
    assert rows[0]["status"] == "committed"
    assert rows[0]["tool_name"] == "send_email"

    # SECOND guard, identical (task, iteration, tool, args) → already_committed.
    fn2 = _Counter("SHOULD-NOT-RUN")
    with activate(ctx):
        r2 = await ledger_guard("send_email", _ARGS, "write", fn2)
    assert fn2.calls == 0  # NOT re-executed
    # The recorded result is replayed back as a ToolResult.
    assert r2.output == "effect-output"
    assert r2.success is True
    # Still exactly one ledger row.
    assert len(await _ledger_rows(pool)) == 1


async def test_consequential_severity_is_guarded(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx = DurableReActContext(task_id="t-cq", owner_id="principal-alice", ledger=ledger)
    fn = _Counter("destructive")
    with activate(ctx):
        await ledger_guard("delete_all", _ARGS, "consequential", fn)
        # replay
        fn2 = _Counter("NOPE")
        r2 = await ledger_guard("delete_all", _ARGS, "consequential", fn2)
    assert fn.calls == 1
    assert fn2.calls == 0
    assert r2.output == "destructive"


# ---- uncertain (intent without commit) ---------------------------------------

async def test_uncertain_intent_without_commit_raises(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx = DurableReActContext(task_id="t-unc", owner_id="principal-alice", ledger=ledger)
    # Simulate a crash AFTER intent but BEFORE commit: write the intent row
    # directly via the real ledger.begin (proceed), then never commit.
    decision = await ledger.begin("t-unc", 0, "charge_card", _ARGS)
    assert decision.outcome == "proceed"  # intent now exists, uncommitted

    fn = _Counter("SHOULD-NOT-RUN")
    with activate(ctx), pytest.raises(DurableReplayUncertain) as ei:
        await ledger_guard("charge_card", _ARGS, "write", fn)
    assert fn.calls == 0  # the possibly-half-done effect is NOT re-run
    assert ei.value.task_id == "t-unc"
    assert ei.value.tool_name == "charge_card"
    assert ei.value.step_index == 0


# ---- contextvars activate / get_active set+reset (async-safe) -----------------

async def test_activate_sets_and_resets(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx = DurableReActContext(task_id="t-ctx", owner_id="principal-alice", ledger=ledger)
    assert get_active() is None
    with activate(ctx):
        assert get_active() is ctx
    assert get_active() is None  # reset on exit


async def test_activate_resets_on_exception(pool: DbPool) -> None:
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx = DurableReActContext(task_id="t-ctx2", owner_id="principal-alice", ledger=ledger)
    with pytest.raises(RuntimeError):  # noqa: SIM117 — assert context cleared after raise
        with activate(ctx):
            assert get_active() is ctx
            raise RuntimeError("boom")
    assert get_active() is None  # reset even on error


async def test_activate_is_async_isolated(pool: DbPool) -> None:
    """Concurrent tasks each see only their own active context."""
    ledger = SideEffectLedger(pool, "principal-alice")
    ctx_a = DurableReActContext(task_id="t-a", owner_id="principal-alice", ledger=ledger)
    ctx_b = DurableReActContext(task_id="t-b", owner_id="principal-alice", ledger=ledger)

    seen: dict[str, str | None] = {}

    async def drive(name: str, ctx: DurableReActContext) -> None:
        with activate(ctx):
            await asyncio.sleep(0)  # yield so the other task interleaves
            active = get_active()
            seen[name] = active.task_id if active else None

    await asyncio.gather(drive("a", ctx_a), drive("b", ctx_b))
    assert seen == {"a": "t-a", "b": "t-b"}
    assert get_active() is None  # outer scope untouched
