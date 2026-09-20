"""WS-B — GoalExecutionHandler delivers its produced answer (Issue 1).

A user-created cron "goal" computes an answer and must deliver it back to the
chat it was scheduled from, exactly-once, via the durable
:class:`ProactiveJobDeliverer` seam — never silently dropped.

Story 4.8 (AD-1) — ``_deliver_answer`` now SUBMITS the declared, irreversible
``notifications.deliver_goal_result`` command instead of calling
``self._job_deliverer.deliver_for_job`` directly, so these tests script the
outcome through a stubbed ``commands/spec/submit.py::submit_command``
(``stub_submit_command``) and inspect the SUBMITTED payload (job/message/
category/urgency) instead of a fake job deliverer's own call log.

These tests pin:

* delivery submits ``notifications.deliver_goal_result`` exactly once with the
  produced response as the message, and the recorded status maps from the
  outcome rollup;
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
from tests._story_7_2_helpers import RecordingDb, StubBackend, disable_guard, make_job, stub_submit_command


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


#: Any non-None placeholder — the REAL job deliverer is never reached once
#: submit_command itself is stubbed; this only needs to satisfy the
#: "_job_deliverer is None" honest-degradation guard in _deliver_answer.
_PLACEHOLDER_DELIVERER = object()


class TestGoalExecutionDelivery:
    async def test_delivers_once_and_records_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        calls = stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="weather: sunny")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]
        job = _targeted_job()

        await handler.execute(job)

        assert len(calls) == 1
        payload = calls[0]["payload"]
        assert payload.message == "weather: sunny"
        assert payload.job.job_id == job.job_id
        assert payload.category == "goal_answer"
        # TS10 — a RECURRING poke (no run_once) routes at "normal" urgency so the
        # NotificationRouter can coalesce it inside quiet hours (see the one-shot
        # case below, which stays "critical").
        assert payload.urgency == "normal"
        assert calls[0]["command_type"] == "notifications.deliver_goal_result"
        assert calls[0]["kwargs"]["authority_scope"] == ("job", job.job_id)
        assert _status_of(db) == "completed"

    async def test_pipeline_state_defers_delivery_and_uses_job_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]
        job = _targeted_job(primary_channel="telegram")

        await handler.execute(job)

        assert len(backend.calls) == 1
        state = backend.calls[0]
        assert state.defer_delivery is True
        assert state.channel == "telegram"
        # FULL job_id in the session, not a truncated prefix (collision fix).
        assert state.session_key == f"goal-{job.job_id}"
        assert job.job_id in state.session_key
        # CHANGED 2026-09-01, deliberately. This read "The handler does NOT set
        # reply_target on a goal state", and the reason given in the handler was to
        # prevent a double-send. That guard is `defer_delivery` itself — `deliver.run`
        # opens with `if state.defer_delivery: return state`, its first statement —
        # so the address was never what stopped the second send.
        #
        # What the absence DID do: `destination_for_turn` saw a bare channel,
        # correctly refused to record it ("A CHANNEL NAME IS NOT AN ADDRESS"), and
        # the durable task was written with destination NULL — so recovery had no
        # target and DISCARDED the answer. Measured: 2,053 characters of the Gmail
        # digest's reply thrown away, and NULL on all but 15 of 1,257 task rows.
        assert state.reply_target == 12345
        # And the guard that actually prevents the double-send is still on.
        assert state.defer_delivery is True

    async def test_owl_name_from_params(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="ok")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]
        job = _targeted_job()
        job.params["owl"] = "scout"

        await handler.execute(job)
        assert backend.calls[0].owl_name == "scout"

    async def test_undeliverable_records_undeliverable_and_preserves_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        stub_submit_command(monkeypatch, rollup="undeliverable", success=True)
        backend = StubBackend(response_text="the answer")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]
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
        stub_submit_command(monkeypatch, rollup="partial", success=False)
        backend = StubBackend(response_text="x")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

        result = await handler.execute(_targeted_job())
        assert _status_of(db) == "partial"
        # A partial delivery is a transient failure → JobResult.success False so
        # the scheduler retries (else a recurring goal silently keeps dropping).
        assert result.success is False

    async def test_failed_delivery_records_failed_and_signals_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        stub_submit_command(monkeypatch, rollup="failed", success=False)
        backend = StubBackend(response_text="x")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

        result = await handler.execute(_targeted_job())
        assert _status_of(db) == "failed"
        assert result.success is False  # transient transport/ledger failure → retry

    async def test_suppressed_records_completed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        stub_submit_command(monkeypatch, rollup="suppressed", success=True)
        backend = StubBackend(response_text="x")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

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

    async def test_run_once_delivers_and_leaves_retirement_to_the_scheduler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        order: list[str] = []

        def _record_submission(record: dict[str, Any]) -> None:
            order.append("deliver")

        stub_submit_command(
            monkeypatch, rollup="delivered", success=True, on_call=_record_submission,
        )
        backend = StubBackend(response_text="ship it")
        db = RecordingDb()

        # Wrap db.execute to record the DELETE position.
        orig_execute = db.execute

        async def _tracking_execute(sql: str, params: tuple[Any, ...] = ()) -> None:
            if "DELETE FROM jobs" in sql:
                order.append("delete")
            await orig_execute(sql, params)

        db.execute = _tracking_execute  # type: ignore[method-assign]

        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]
        job = _targeted_job(params={"goal": "ship it", "run_once": True})

        await handler.execute(job)

        assert "deliver" in order
        # The scheduler retires a run_once row after success (2026-09-12); the
        # handler no longer deletes it, so delivery can never lose that race.
        assert "delete" not in order

    async def test_run_once_stays_critical_urgency(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # TS10 — a one-shot goal (run_once) is a direct user request: it stays
        # "critical" so it is delivered promptly and never quiet-hours batched.
        disable_guard(monkeypatch)
        calls = stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="ship it")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]
        job = _targeted_job(params={"goal": "ship it", "run_once": True})

        await handler.execute(job)

        assert calls[0]["payload"].urgency == "critical"

    async def test_empty_response_skips_delivery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        calls = stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

        await handler.execute(_targeted_job())
        # Nothing produced → nothing delivered.
        assert calls == []

    async def test_no_notify_sentinel_skips_delivery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A goal whose own instructions say "stay silent unless X" and the
        # model determined X isn't met must not be delivered — reproduces the
        # reported bug where a "should not send" analysis got sent anyway.
        disable_guard(monkeypatch)
        calls = stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="NO_NOTIFY_NEEDED")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

        result = await handler.execute(_targeted_job())
        assert calls == []
        assert _status_of(db) == "completed"
        assert result.success is True

    async def test_no_notify_sentinel_tolerates_surrounding_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        disable_guard(monkeypatch)
        calls = stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="  NO_NOTIFY_NEEDED\n")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

        await handler.execute(_targeted_job())
        assert calls == []

    async def test_text_merely_containing_the_sentinel_word_still_delivers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The sentinel must be an exact, whole-message match — a real answer
        # that happens to mention the word must still be delivered normally.
        disable_guard(monkeypatch)
        calls = stub_submit_command(monkeypatch, rollup="delivered", success=True)
        backend = StubBackend(response_text="NO_NOTIFY_NEEDED is not a real ticker symbol.")
        db = RecordingDb()
        handler = GoalExecutionHandler(backend=backend, db=db, job_deliverer=_PLACEHOLDER_DELIVERER)  # type: ignore[arg-type]

        await handler.execute(_targeted_job())
        assert len(calls) == 1
