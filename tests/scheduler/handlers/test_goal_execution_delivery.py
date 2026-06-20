"""WS-B — GoalExecutionHandler delivers its produced answer (Issue 1).

A user-created cron "goal" computes an answer and must deliver it back to the
chat it was scheduled from, exactly-once, via the durable
:class:`ProactiveJobDeliverer` seam — never silently dropped.

These tests pin:

* delivery happens through ``deliver_for_job`` exactly once with the produced
  response as the message, and the recorded status maps from the outcome rollup;
* the PipelineState the backend runs uses ``defer_delivery=True`` (the pipeline
  deliver step no-ops; THIS handler owns delivery), a FULL-job_id session, and
  the channel from the job (not hardcoded "cli");
* an honesty invariant: a body that could not be delivered records
  ``undeliverable`` (never ``completed``), result_text preserved, no crash;
* the legacy/no-deliverer surface still records ``completed`` and never sends;
* delivery happens BEFORE the run_once job-row delete (target not lost).
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from tests._story_7_2_helpers import RecordingDb, StubBackend, disable_guard, make_job


class FakeJobDeliverer:
    """Records ``deliver_for_job`` calls and returns a scripted outcome rollup."""

    def __init__(self, rollup: str = "delivered") -> None:
        self._rollup = rollup
        self.calls: list[dict[str, Any]] = []

    async def deliver_for_job(
        self,
        job: Any,
        *,
        message: str,
        category: str,
        urgency: str = "normal",
    ) -> Any:
        self.calls.append(
            {
                "job": job,
                "message": message,
                "category": category,
                "urgency": urgency,
            }
        )
        from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome

        return ProactiveDeliveryOutcome(rollup=self._rollup)


def _targeted_job(*, params: dict[str, Any] | None = None, **overrides: Any) -> Any:
    return make_job(
        params=params or {"goal": "Check the weather"},
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
        **overrides,
    )


def _status_of(db: RecordingDb) -> str:
    inserts = [e for e in db.executes if "INSERT INTO job_results" in e[0]]
    assert len(inserts) == 1
    return str(inserts[0][1][2])


def _result_text_of(db: RecordingDb) -> Any:
    inserts = [e for e in db.executes if "INSERT INTO job_results" in e[0]]
    assert len(inserts) == 1
    return inserts[0][1][3]


pytestmark = pytest.mark.asyncio


class TestGoalExecutionDelivery:
    async def test_delivers_once_and_records_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="weather: sunny")
        db = RecordingDb()
        deliverer = FakeJobDeliverer(rollup="delivered")
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]
        job = _targeted_job()

        await handler.execute(job)

        assert len(deliverer.calls) == 1
        call = deliverer.calls[0]
        assert call["message"] == "weather: sunny"
        assert call["job"] is job
        assert call["category"] == "goal_answer"
        assert call["urgency"] == "critical"
        assert _status_of(db) == "completed"

    async def test_pipeline_state_defers_delivery_and_uses_job_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        deliverer = FakeJobDeliverer()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]
        job = _targeted_job(primary_channel="telegram")

        await handler.execute(job)

        assert len(backend.calls) == 1
        state = backend.calls[0]
        assert state.defer_delivery is True
        assert state.channel == "telegram"
        # FULL job_id in the session, not a truncated prefix (collision fix).
        assert state.session_id == f"goal-{job.job_id}"
        assert job.job_id in state.session_id
        # The handler does NOT set reply_target on a goal state.
        assert state.reply_target is None

    async def test_owl_name_from_params(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        deliverer = FakeJobDeliverer()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]
        job = _targeted_job()
        job.params["owl"] = "scout"

        await handler.execute(job)
        assert backend.calls[0].owl_name == "scout"

    async def test_undeliverable_records_undeliverable_and_preserves_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="the answer")
        db = RecordingDb()
        deliverer = FakeJobDeliverer(rollup="undeliverable")
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]
        # Empty targets — nothing to deliver to.
        job = make_job(params={"goal": "do it"})

        result = await handler.execute(job)

        assert _status_of(db) == "undeliverable"
        # Answer never lost from the /agents log.
        assert _result_text_of(db) == "the answer"
        # No crash; work succeeded even though delivery didn't.
        assert result.success is True

    async def test_partial_records_partial_and_signals_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="x")
        db = RecordingDb()
        deliverer = FakeJobDeliverer(rollup="partial")
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]

        result = await handler.execute(_targeted_job())
        assert _status_of(db) == "partial"
        # A partial delivery is a transient failure → JobResult.success False so
        # the scheduler retries (else a recurring goal silently keeps dropping).
        assert result.success is False

    async def test_failed_delivery_records_failed_and_signals_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="x")
        db = RecordingDb()
        deliverer = FakeJobDeliverer(rollup="failed")
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]

        result = await handler.execute(_targeted_job())
        assert _status_of(db) == "failed"
        assert result.success is False  # transient transport/ledger failure → retry

    async def test_suppressed_records_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="x")
        db = RecordingDb()
        deliverer = FakeJobDeliverer(rollup="suppressed")
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]

        await handler.execute(_targeted_job())
        assert _status_of(db) == "completed"

    async def test_legacy_no_deliverer_no_targets_records_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        # No job_deliverer wired AND no targets (true legacy Story 7.2 surface):
        # nothing was ever meant to be delivered → completed, no send.
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = make_job(params={"goal": "ok"})

        result = await handler.execute(job)
        assert _status_of(db) == "completed"
        assert result.success is True

    async def test_no_deliverer_with_targets_records_undeliverable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="the answer")
        db = RecordingDb()
        # Wiring gap: the job WAS created with a delivery target but no deliverer
        # is wired. Honesty: record undeliverable, never a fake "completed".
        handler = GoalExecutionHandler(backend=backend, db=db)  # type: ignore[arg-type]
        job = _targeted_job()

        await handler.execute(job)
        assert _status_of(db) == "undeliverable"
        assert _result_text_of(db) == "the answer"  # answer preserved

    async def test_delivery_before_run_once_delete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="ship it")
        db = RecordingDb()

        order: list[str] = []

        class _OrderingDeliverer(FakeJobDeliverer):
            async def deliver_for_job(self, job: Any, **kw: Any) -> Any:  # type: ignore[override]
                order.append("deliver")
                return await super().deliver_for_job(job, **kw)

        # Wrap db.execute to record the DELETE position.
        orig_execute = db.execute

        async def _tracking_execute(sql: str, params: tuple[Any, ...] = ()) -> None:
            if "DELETE FROM jobs" in sql:
                order.append("delete")
            await orig_execute(sql, params)

        db.execute = _tracking_execute  # type: ignore[method-assign]

        deliverer = _OrderingDeliverer()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]
        job = _targeted_job(params={"goal": "ship it", "run_once": True})

        await handler.execute(job)

        assert "deliver" in order
        assert "delete" in order
        assert order.index("deliver") < order.index("delete")

    async def test_empty_response_skips_delivery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        backend = StubBackend(response_text="")
        db = RecordingDb()
        deliverer = FakeJobDeliverer()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=deliverer)  # type: ignore[arg-type]

        await handler.execute(_targeted_job())
        # Nothing produced → nothing delivered.
        assert deliverer.calls == []
