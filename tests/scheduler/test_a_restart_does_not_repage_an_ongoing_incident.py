"""The re-alert backoff is one hour. He got 37 pages where 13 were intended.

``HealthSweepHandler`` alerts on unhealthy subsystems and deliberately re-alerts
on a heartbeat while an incident is ongoing — ``realert_backoff_s = 3600.0``. The
state enforcing that is::

    # Plain in-memory dict — no new store/table; it doesn't need to survive a
    # restart (a fresh process re-alerts once on the next unhealthy tick, which
    # is fine).
    self._alert_state: dict[str, tuple[str, float]] = {}

The parenthesis is the defect, and it is an assumption about restart frequency
that was never measured.

MEASURED 2026-09-03, over the provider outage (10:00–23:17 UTC, one continuous
incident, ``provider:NeraAiRaw`` down throughout):

    critical operator_health pages delivered to Telegram    37
    intended by the one-hour heartbeat over 13 hours        13
    pages landing within 3 minutes of a process boot        11  (29%)

    observed gaps, most recent first (minutes)
        5.5, 5.5, 27.3, 10.6, 16.5, 5.5, 5.4

A restart empties ``_alert_state`` AND resets the monotonic clock it stores, so
the next sweep sees ``prior is None``, calls it a new incident, and pages
immediately. CodeWatcher exec-replaces the core on every code change, so "once
per restart" is not a rare extra — on this day it was the dominant source.

THE CAUSE IS PER-PROCESS STATE DOING A DURABLE JOB. That is the sibling
handler's own phrase about its own first version: ``capability_gap_escalation``
records "The refusal WAS recorded ... into a ContextVar that reset() clears when
the turn ends ... Per-turn state doing a durable job — this codebase's first
shape, one scope too narrow rather than absent." It fixed that by persisting to
``audit_log``. This is the same shape one scope up, and it takes the same cure —
no new store, no new engine.

NOTHING IS WEAKENED, and this is the part that matters given the standing
"FAILS TOWARD PAGING" rule. A subsystem with no prior record still pages
immediately. A LEVEL CHANGE (degraded -> down) still bypasses the backoff. Only
an identical, already-reported, still-ongoing incident is held inside its own
declared hour — which is exactly what the in-memory version already did within a
single process. Surviving a restart does not suppress anything the design did not
already intend to suppress; it stops an unrelated event from resetting the clock.
"""

from __future__ import annotations

import pytest

from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler

pytestmark = pytest.mark.asyncio

BACKOFF_S = 3600.0


class _Clock:
    def __init__(self, t: float = 10_000.0) -> None:
        self.t = t

    def monotonic(self) -> float:
        return self.t

    def now(self):  # pragma: no cover — some Clock users want wall time
        import datetime
        return datetime.datetime.now(tz=datetime.UTC)


class _Aggregator:
    def __init__(self, statuses: list[HealthStatus]) -> None:
        self._statuses = statuses

    async def collect(self) -> list[HealthStatus]:
        return list(self._statuses)


def _down(name: str = "provider:NeraAiRaw") -> HealthStatus:
    return HealthStatus(name=name, status="down", message="unreachable", latency_ms=1.0)


class _Recorder:
    """Stands in for the durable record of what the operator was already sent."""

    def __init__(self, prior: dict[str, tuple[str, float]] | None = None) -> None:
        # name -> (status, seconds ago)
        self.prior = dict(prior or {})
        self.written: list[tuple[str, str]] = []

    async def load_recent_alerts(self, within_s: float) -> dict[str, tuple[str, float]]:
        return {k: v for k, v in self.prior.items() if v[1] <= within_s}

    def record_alert(self, name: str, status: str) -> None:
        self.written.append((name, status))


def _handler(agg, recorder, clock, sent: list[str]):
    async def _alert(msg: str) -> None:
        sent.append(msg)

    return HealthSweepHandler(
        agg, alert=_alert, clock=clock, realert_backoff_s=BACKOFF_S,
        alert_record=recorder,
    )


async def _sweep(h) -> None:
    import datetime as _dt

    from stackowl.scheduler.job import Job
    _now = _dt.datetime.now(tz=_dt.UTC)
    await h.execute(Job(
        job_id="health_sweep-t", handler_name="health_sweep", schedule="every 5m",
        idempotency_key="k", last_run_at=None, next_run_at=_now.isoformat(),
        status="pending",
    ))


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


async def test_a_fresh_process_does_not_repage_an_incident_already_reported() -> None:
    """THE DEFECT. A restart 10 minutes into an hour-long backoff paged him again
    — and on 2026-09-03 that happened 11 times inside one outage."""
    sent: list[str] = []
    rec = _Recorder({"provider:NeraAiRaw": ("down", 600.0)})  # alerted 10 min ago
    await _sweep(_handler(_Aggregator([_down()]), rec, _Clock(), sent))

    assert sent == [], (
        "a fresh process re-paged an incident the operator was told about 10 "
        f"minutes ago, inside its own 60-minute backoff: {sent}"
    )


async def test_the_heartbeat_still_fires_once_the_hour_has_passed() -> None:
    """The backoff is a heartbeat, not a mute. "Still down" after an hour is
    exactly what the design wants said."""
    sent: list[str] = []
    rec = _Recorder({"provider:NeraAiRaw": ("down", BACKOFF_S + 60.0)})
    await _sweep(_handler(_Aggregator([_down()]), rec, _Clock(), sent))

    assert len(sent) == 1, sent


async def test_an_unreported_subsystem_still_pages_immediately() -> None:
    """FAIL TOWARD PAGING is untouched. A subsystem with no durable record is a
    new incident and must reach him at once — the cost the codebase explicitly
    chose to pay."""
    sent: list[str] = []
    await _sweep(_handler(_Aggregator([_down()]), _Recorder({}), _Clock(), sent))
    assert len(sent) == 1, sent


async def test_a_level_change_still_bypasses_the_backoff() -> None:
    """degraded -> down is new information however recently he was paged."""
    sent: list[str] = []
    rec = _Recorder({"provider:NeraAiRaw": ("degraded", 60.0)})
    await _sweep(_handler(_Aggregator([_down()]), rec, _Clock(), sent))
    assert len(sent) == 1, sent


async def test_sending_an_alert_records_it_durably() -> None:
    """A backoff that reads a record nothing writes would suppress nothing. The
    write is the half that makes the next process's read meaningful."""
    sent: list[str] = []
    rec = _Recorder({})
    await _sweep(_handler(_Aggregator([_down()]), rec, _Clock(), sent))
    assert rec.written == [("provider:NeraAiRaw", "down")], rec.written


async def test_no_recorder_behaves_exactly_as_before() -> None:
    """The durable record is optional wiring. Without it the handler must keep
    its in-memory behaviour byte-for-byte, so an unwired deployment is unchanged."""
    sent: list[str] = []
    h = HealthSweepHandler(
        _Aggregator([_down()]), alert=lambda m: _noop(sent, m),
        clock=_Clock(), realert_backoff_s=BACKOFF_S,
    )
    await _sweep(h)
    assert len(sent) == 1


async def _noop(sent: list[str], msg: str) -> None:
    sent.append(msg)


@pytest.mark.tripwire
def test_the_record_is_actually_WIRED_into_the_sweep() -> None:
    """A backoff that survives restarts, built and not connected, would be
    decoration — and the suite above passes just as happily unwired, because
    ``test_no_recorder_behaves_exactly_as_before`` asserts the unwired path is
    unchanged. That is precisely the "built but not wired" shape this codebase
    lists first among its recurring defects, so the wiring is asserted, not
    assumed."""
    import inspect

    from stackowl.scheduler import assembly

    src = inspect.getsource(assembly)
    assert "alert_record=AuditAlertRecord(" in src, (
        "HealthSweepHandler is constructed without a durable alert record — the "
        "backoff still resets on every restart"
    )


async def test_the_recorded_AGE_is_preserved_not_reset_to_now() -> None:
    """CAUGHT BY MUTATION. Seeding with ``now`` instead of ``now - age`` passes
    every test above — a level change still bypasses, a new subsystem still
    pages — while silently restarting the hour for an incident already 59 minutes
    into it. The restart would then DELAY the heartbeat rather than duplicate it,
    which is the opposite error and the more dangerous direction under a
    fail-toward-paging rule.

    The durable record carries an age for a reason; dropping it is a different
    bug wearing the fix's clothes."""
    sent: list[str] = []
    clock = _Clock()
    rec = _Recorder({"provider:NeraAiRaw": ("down", BACKOFF_S - 60.0)})  # 59 min in
    h = _handler(_Aggregator([_down()]), rec, clock, sent)

    await _sweep(h)          # 59 min in — correctly silent
    assert sent == [], sent

    clock.t += 120.0         # now past the hour
    await _sweep(h)

    assert len(sent) == 1, (
        "the heartbeat did not fire after the declared hour — the age carried by "
        "the durable record was discarded and the backoff restarted from the seed"
    )
