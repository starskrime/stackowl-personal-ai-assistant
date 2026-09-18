"""Story 2.6 — the health sweep's heal.attempted/healed/exhausted and
health.changed journal events. Mirrors ``test_health_loop.py``'s ADR-6 fakes
(``_ScriptedAggregator``/``_FakeHealable``), checking the ``journal_events``
table each real transition lands in, in the same transaction as its own
``heal_attempts``/``health_status_changes`` row (AD-24).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import stackowl.config.settings as settings_mod
from stackowl.db.pool import DbPool
from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


class _ScriptedAggregator:
    def __init__(self, *rounds: list[HealthStatus]) -> None:
        self._rounds = list(rounds)
        self.collects = 0

    async def collect(self) -> list[HealthStatus]:
        idx = min(self.collects, len(self._rounds) - 1)
        self.collects += 1
        return self._rounds[idx]


class _FakeHealable:
    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.unavailable_reason = None if available else "dead"
        self.ensures = 0
        self._heals = True

    async def ensure_available(self) -> None:
        self.ensures += 1
        if self._heals:
            self.available = True
            self.unavailable_reason = None

    def register_on_recycled(self, cb) -> None:  # noqa: ANN001
        pass


def _job() -> Job:
    return Job(
        job_id="hsj-1", handler_name="health_sweep", schedule="every 5m",
        idempotency_key="health_sweep:every-5m", last_run_at=None,
        next_run_at="2026-06-27T00:00:00+00:00", status="pending",
    )


@pytest.fixture
def _flag(monkeypatch):  # noqa: ANN202
    def _set(value: bool) -> None:
        monkeypatch.setattr(
            settings_mod, "Settings", lambda: SimpleNamespace(health_loop=value)
        )
    return _set


async def _journal_rows(db: DbPool, event_type: str, target_id: str) -> list[dict]:
    return await db.fetch_all(
        "SELECT * FROM journal_events WHERE type = ? AND target_id = ?",
        (event_type, target_id),
    )


class TestHealAttemptedThenHealed:
    async def test_a_recovered_subsystem_writes_attempted_then_healed(
        self, _flag, tmp_db: DbPool,
    ) -> None:
        _flag(True)
        agg = _ScriptedAggregator(
            [HealthStatus("db", "down", "pool wedged", 5000.0)],
            [HealthStatus("db", "ok", None, 1.0)],
        )
        healer = _FakeHealable(available=False)
        handler = HealthSweepHandler(agg, alert=None, healers={"db": healer}, db=tmp_db)

        await handler.execute(_job())

        attempted = await _journal_rows(tmp_db, "heal.attempted", "db")
        assert len(attempted) == 1
        assert attempted[0]["outcome"] == "pending"
        ref = json.loads(attempted[0]["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["table"] == "heal_attempts"

        healed = await _journal_rows(tmp_db, "heal.healed", "db")
        assert len(healed) == 1
        assert healed[0]["outcome"] == "healed"
        assert await _journal_rows(tmp_db, "heal.exhausted", "db") == []

        rows = await tmp_db.fetch_all(
            "SELECT status FROM heal_attempts WHERE subsystem = ?", ("db",),
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "healed"


class TestHealExhaustedIsTheMissingBranch:
    async def test_a_still_unhealthy_subsystem_writes_exhausted_needs_you_high(
        self, _flag, tmp_db: DbPool,
    ) -> None:
        _flag(True)
        agg = _ScriptedAggregator(
            [HealthStatus("db", "down", "pool wedged", 5000.0)],
            [HealthStatus("db", "down", "pool wedged", 5000.0)],
        )
        healer = _FakeHealable(available=False)
        healer._heals = False
        handler = HealthSweepHandler(agg, alert=None, healers={"db": healer}, db=tmp_db)

        await handler.execute(_job())

        exhausted = await _journal_rows(tmp_db, "heal.exhausted", "db")
        assert len(exhausted) == 1
        assert exhausted[0]["attention"] == "needs_you"
        assert exhausted[0]["intensity"] == "high"
        assert await _journal_rows(tmp_db, "heal.healed", "db") == []

        rows = await tmp_db.fetch_all(
            "SELECT status FROM heal_attempts WHERE subsystem = ?", ("db",),
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "exhausted"

    async def test_a_subsystem_with_no_healer_never_enters_the_heal_loop(
        self, _flag, tmp_db: DbPool,
    ) -> None:
        """No healer registered at all -- the ordinary down/degraded alert
        path, never heal.*, matching the pre-ADR-6 byte-identical case."""
        _flag(True)
        agg = _ScriptedAggregator([HealthStatus("graph", "down", "kuzu gone", 2.0)])
        handler = HealthSweepHandler(agg, alert=None, healers={}, db=tmp_db)

        await handler.execute(_job())

        assert await _journal_rows(tmp_db, "heal.attempted", "graph") == []
        assert await _journal_rows(tmp_db, "heal.exhausted", "graph") == []


class TestHealthChangedFiresOnlyOnARealTransition:
    async def test_first_observation_records_nothing(self, tmp_db: DbPool) -> None:
        agg = _ScriptedAggregator([HealthStatus("db", "ok", None, 1.0)])
        handler = HealthSweepHandler(agg, alert=None, db=tmp_db)

        await handler.execute(_job())

        assert await _journal_rows(tmp_db, "health.changed", "db") == []
        # But a baseline row now exists for a FUTURE tick to compare against.
        rows = await tmp_db.fetch_all(
            "SELECT previous_status, new_status FROM health_status_changes "
            "WHERE subsystem = ?", ("db",),
        )
        assert len(rows) == 1
        assert rows[0]["previous_status"] == rows[0]["new_status"] == "ok"

    async def test_an_unchanged_tick_records_nothing(self, tmp_db: DbPool) -> None:
        agg = _ScriptedAggregator([HealthStatus("db", "ok", None, 1.0)])
        handler = HealthSweepHandler(agg, alert=None, db=tmp_db)
        await handler.execute(_job())  # seeds the baseline row

        await handler.execute(_job())  # SAME status again

        assert await _journal_rows(tmp_db, "health.changed", "db") == []
        rows = await tmp_db.fetch_all(
            "SELECT COUNT(*) AS n FROM health_status_changes WHERE subsystem = ?", ("db",),
        )
        assert rows[0]["n"] == 1, "an unchanged tick must not add a row either"

    async def test_a_real_transition_records_previous_new_and_error_code(
        self, tmp_db: DbPool,
    ) -> None:
        agg = _ScriptedAggregator([HealthStatus("db", "ok", None, 1.0)])
        handler = HealthSweepHandler(agg, alert=None, db=tmp_db)
        await handler.execute(_job())  # seeds baseline: ok

        agg._rounds = [[HealthStatus("db", "down", "connection refused", 5000.0)]]
        agg.collects = 0
        await handler.execute(_job())

        rows = await _journal_rows(tmp_db, "health.changed", "db")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["previous_status"] == "ok"
        assert attrs["new_status"] == "down"
        assert attrs["error_code"] == "connection_refused"
        ref = json.loads(rows[0]["record_ref"])
        assert ref["locator"]["table"] == "health_status_changes"

    async def test_a_recovery_transition_is_healed_outcome_with_no_error_code(
        self, tmp_db: DbPool,
    ) -> None:
        agg = _ScriptedAggregator([HealthStatus("db", "down", "boom", 5000.0)])
        handler = HealthSweepHandler(agg, alert=None, db=tmp_db)
        await handler.execute(_job())  # seeds baseline: down

        agg._rounds = [[HealthStatus("db", "ok", None, 1.0)]]
        agg.collects = 0
        await handler.execute(_job())

        rows = await _journal_rows(tmp_db, "health.changed", "db")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "healed"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["previous_status"] == "down"
        assert attrs["new_status"] == "ok"
        assert attrs["error_code"] is None

    async def test_never_carries_raw_exception_text(self, tmp_db: DbPool) -> None:
        """FR84/AD-4 — the message itself never reaches attrs, only the closed code."""
        agg = _ScriptedAggregator([HealthStatus("db", "ok", None, 1.0)])
        handler = HealthSweepHandler(agg, alert=None, db=tmp_db)
        await handler.execute(_job())

        secret_message = "Traceback: /home/owner/secret-path/credentials.yaml"
        agg._rounds = [[HealthStatus("db", "down", secret_message, 5000.0)]]
        agg.collects = 0
        await handler.execute(_job())

        rows = await _journal_rows(tmp_db, "health.changed", "db")
        assert len(rows) == 1
        assert secret_message not in rows[0]["attrs"]


class TestAForcedRollbackLeavesNoEvent:
    async def test_a_failing_journal_record_rolls_back_the_heal_attempts_row_too(
        self, _flag, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same shape as ``tests/pipeline/durable/test_journal_wiring.py``'s
        real-call-site rollback proof, applied to the heal.attempted call site."""
        import stackowl.scheduler.handlers.health_sweep as health_sweep_module

        _flag(True)
        agg = _ScriptedAggregator([HealthStatus("db", "down", "pool wedged", 5000.0)])
        healer = _FakeHealable(available=False)
        handler = HealthSweepHandler(agg, alert=None, healers={"db": healer}, db=tmp_db)

        async def _boom(conn: object, event: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(health_sweep_module, "journal_record", _boom)

        with pytest.raises(RuntimeError, match="simulated journal.record failure"):
            await handler._record_heal_attempted("db")

        assert await _journal_rows(tmp_db, "heal.attempted", "db") == []
        rows = await tmp_db.fetch_all(
            "SELECT 1 FROM heal_attempts WHERE subsystem = ?", ("db",),
        )
        assert rows == []
