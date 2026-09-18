"""``NeedsYouExpirySweepHandler`` -- the seeded expiry-sweep job (Story 3.2,
AD-28).

Mirrors ``tests/scheduler/handlers/test_journal_prune.py``'s own shape: real
expired rows are swept through the same resolver path a real answer uses,
a forced failure never raises out of ``execute()`` (a failed ``JobResult``
instead), and ``JobResult.success`` reflects the outcome.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import ActorKind, JournalEvent, Outcome, record
from stackowl.journal import needs_you as needs_you_module
from stackowl.journal.heal_events import HealExhaustedAttrs
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.needs_you_expiry_sweep import (
    NeedsYouExpirySweepHandler,
    register_needs_you_expiry_sweep_handler,
)
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """``HandlerRegistry`` is a process-global singleton -- reset around every
    test so one test's registration can never leak into another (mirrors
    ``test_journal_prune.py``'s own autouse reset)."""
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


@pytest.fixture()
async def db(tmp_path: Path, _migrated_template: Path) -> AsyncGenerator[DbPool]:
    """Same shape as ``tests/conftest.py``'s own ``tmp_db`` fixture -- named
    ``db`` locally to read cleanly at every call site below."""
    from tests.conftest import seed_migrated_db

    db_path = seed_migrated_db(
        tmp_path / "needs_you_expiry_sweep_test.db", _migrated_template,
    )
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _job() -> Job:
    return Job(
        job_id="needs_you_expiry_sweep-test",
        handler_name="needs_you_expiry_sweep",
        schedule="every 1m",
        idempotency_key="needs_you_expiry_sweep:every-1m",
        last_run_at=None,
        next_run_at="2026-01-01T00:00:00+00:00",
        status="pending",
    )


async def _open_incident_item(db: DbPool, target_id: str) -> str:
    """Open one real incident item via the production ``journal.record``
    API (not a hand-typed INSERT), and return its id."""
    async with db.transaction() as conn:
        await record(conn, JournalEvent(
            type="heal.exhausted", schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS, actor_id="health_sweep",
            target_kind=ActorKind.OWNER, target_id=target_id,
            outcome=Outcome.FAILED, attrs=HealExhaustedAttrs(attempt_count=1),
        ))
    rows = await db.fetch_all(
        "SELECT id FROM needs_you WHERE dedupe_key = ?",
        (f"incident:owner:{target_id}",),
    )
    return str(rows[0]["id"])


class TestANormalSweepPass:
    async def test_sweeps_real_expired_rows_and_leaves_fresh_ones_open(
        self, db: DbPool,
    ) -> None:
        stale_id = await _open_incident_item(db, "sweep-handler-stale")
        fresh_id = await _open_incident_item(db, "sweep-handler-fresh")
        await db.execute(
            "UPDATE needs_you SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", stale_id),
        )

        result = await NeedsYouExpirySweepHandler(db).execute(_job())

        assert result.success is True
        assert result.metadata["expired_count"] == 1
        assert result.metadata["expired_ids"] == [stale_id]

        rows = await db.fetch_all(
            "SELECT id, resolved_cursor FROM needs_you WHERE id IN (?, ?)",
            (stale_id, fresh_id),
        )
        by_id = {r["id"]: r["resolved_cursor"] for r in rows}
        assert by_id[stale_id] is not None, "the stale row must be resolved"
        assert by_id[fresh_id] is None, "a row with no expires_at must survive"

    async def test_nothing_stale_means_zero_expired(self, db: DbPool) -> None:
        await _open_incident_item(db, "sweep-handler-nothing-stale")

        result = await NeedsYouExpirySweepHandler(db).execute(_job())

        assert result.success is True
        assert result.metadata["expired_count"] == 0
        assert result.metadata["expired_ids"] == []


class TestNeverRaisesOutOfExecute:
    """Mirrors ``test_journal_prune.py``'s own never-fail-a-tick contract:
    a forced underlying failure comes back as a failed ``JobResult``, never
    an exception escaping ``execute()``."""

    async def test_a_forced_failure_returns_a_failed_jobresult_not_a_raise(
        self, db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _boom(*args: object, **kwargs: object) -> list[str]:
            raise RuntimeError("simulated sweep failure")

        # The handler calls `needs_you.sweep_expired_items(...)` through the
        # SAME module object imported here -- patching this attribute is
        # visible through that reference too.
        monkeypatch.setattr(needs_you_module, "sweep_expired_items", _boom)

        result = await NeedsYouExpirySweepHandler(db).execute(_job())

        assert result.success is False
        assert result.error is not None
        assert "simulated sweep failure" in result.error
        assert result.metadata["expired_count"] == 0


class TestRegistration:
    async def test_register_needs_you_expiry_sweep_handler_registers_it(
        self, db: DbPool,
    ) -> None:
        register_needs_you_expiry_sweep_handler(db)
        handler = HandlerRegistry.instance().get("needs_you_expiry_sweep")
        assert handler is not None
        assert handler.handler_name == "needs_you_expiry_sweep"
