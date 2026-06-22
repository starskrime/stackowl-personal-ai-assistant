"""Phase L — heavy jobs defer to live user turns (with a starvation cap)."""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.scheduler.job import Job
from stackowl.scheduler.scheduler import JobScheduler


def _job(handler_name: str, *, overdue_s: float = 0.0) -> Job:
    due = (datetime.now(UTC) - timedelta(seconds=overdue_s)).isoformat()
    return Job(
        job_id="j1",
        handler_name=handler_name,
        schedule="every 30m",
        idempotency_key="k1",
        last_run_at=None,
        next_run_at=due,
        status="pending",
    )


class _Handler:
    def __init__(self, name: str, *, heavy: bool) -> None:
        self._name = name
        self._heavy = heavy

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def defer_under_load(self) -> bool:
        return self._heavy


def _registry(*handlers: _Handler) -> object:
    table = {h.handler_name: h for h in handlers}
    return types.SimpleNamespace(get=table.get)


def _scheduler(registry: object, *, busy: bool, max_defer_sec: float = 900.0) -> JobScheduler:
    turn_registry = types.SimpleNamespace(has_active_turns=lambda: busy)
    return JobScheduler(
        db=object(),  # unused by _should_defer_under_load
        handler_registry=registry,  # type: ignore[arg-type]
        turn_registry=turn_registry,
        max_defer_sec=max_defer_sec,
    )


def test_heavy_job_deferred_while_turn_active() -> None:
    sched = _scheduler(_registry(_Handler("dream", heavy=True)), busy=True)
    assert sched._should_defer_under_load(_job("dream")) is True


def test_heavy_job_runs_when_idle() -> None:
    sched = _scheduler(_registry(_Handler("dream", heavy=True)), busy=False)
    assert sched._should_defer_under_load(_job("dream")) is False


def test_light_job_runs_even_when_busy() -> None:
    sched = _scheduler(_registry(_Handler("sweep", heavy=False)), busy=True)
    assert sched._should_defer_under_load(_job("sweep")) is False


def test_starvation_cap_runs_overdue_heavy_job() -> None:
    # Overdue beyond max_defer_sec → run anyway even under load.
    sched = _scheduler(_registry(_Handler("dream", heavy=True)), busy=True, max_defer_sec=900.0)
    assert sched._should_defer_under_load(_job("dream", overdue_s=1200.0)) is False


def test_no_turn_registry_never_defers() -> None:
    sched = JobScheduler(db=object(), handler_registry=_registry(_Handler("dream", heavy=True)))  # type: ignore[arg-type]
    assert sched._should_defer_under_load(_job("dream")) is False


def test_unknown_handler_not_deferred() -> None:
    sched = _scheduler(_registry(), busy=True)
    assert sched._should_defer_under_load(_job("ghost")) is False


def test_turn_registry_active_accessors() -> None:
    tr = TurnRegistry()
    assert tr.has_active_turns() is False
    assert tr.active_turn_count() == 0
    tr._running["sess-1"] = "req-1"  # simulate a running turn
    assert tr.has_active_turns() is True
    assert tr.active_turn_count() == 1
