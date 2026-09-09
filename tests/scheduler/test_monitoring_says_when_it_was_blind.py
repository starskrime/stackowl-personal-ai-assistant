"""Monitoring went blind for three hours and said nothing when it came back.

MEASURED 2026-09-09 against the retained logs, on the incident of 2026-09-07.

    health_sweep entries/hour   00..13: 10-11 each     14: 5   15: 0   16: 0   17: 0   18: 4
    DB connect failures/hour                            14: 419 15: 838 16: 839 17: 838 18: 520

For THREE COMPLETE HOURS the sweep did not run at all, while the database failed
838 times an hour. The monitor was silent exactly when there was most to report —
3,489 `pool._open_inside_lock: connect failed`, 2,974 `[loop] tick failed` and 491
`_poll_cycle: poll raised` across the window.

THE MONITOR SHARES A FAILURE DOMAIN WITH THE THING IT MONITORS. The sweep is a
scheduled job; the scheduler reads `jobs` from the database to dispatch it; the
database was down. And the check that would have noticed — `store_cadence`, which
already watches `jobs.last_run_at` for staleness — is a CONTRIBUTOR TO THE SWEEP, so
it does not run either. The detector for "the sweep stopped" was inside the sweep.

WHAT CAN AND CANNOT BE FIXED FROM IN HERE. Nothing inside the process can report
while the process cannot reach its database, and the external-watchdog half is
already settled: ESC-159 was answered "leave this box alone — the question is the
CUSTOMER deployment". So this does NOT add a watchdog.

YOU CANNOT DETECT YOUR OWN ABSENCE WHILE ABSENT — BUT YOU CAN DETECT IT ON RETURN,
and nothing did. When the sweep resumed at 18:37 it logged an ordinary exit, exactly
like the 10-11 before the incident. Nothing in the record said "there was no
monitoring for four hours", so the gap was recoverable only by counting log lines per
hour by hand, which is what this file's header is.

`Job.last_run_at` holds the PREVIOUS run's timestamp at execute time — the completion
UPDATE that overwrites it runs after the handler — so the gap needs no new plumbing
and no second source. The expected interval comes from `parse_every`, the same
function `compute_next_run` and `is_valid_schedule` use, so the sweep cannot disagree
with the scheduler about its own cadence.

MISSED RUNS, NOT SECONDS. A threshold in seconds would be a constant nobody could
justify and would rot the moment the cadence changed; `gap / interval - 1` is derived
from the job's own schedule, so a 5-minute sweep and a daily one are both correct
without a second number. Two missed runs is the floor, because one is jitter.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from stackowl.scheduler.handlers import health_sweep
from stackowl.scheduler.job import Job


def _job(last_run_at: str | None, schedule: str = "every 5m") -> Job:
    # `Job` is a pydantic model, and `idempotency_key`/`next_run_at` are REQUIRED
    # `str` rather than optional — caught by reading the model before applying the
    # change, not by a failing run.
    return Job(
        job_id="health_sweep-f2d80205",
        handler_name="health_sweep",
        schedule=schedule,
        idempotency_key="health_sweep",
        last_run_at=last_run_at,
        next_run_at=_iso(-300),
        status="running",
    )


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class TestTheGapIsMeasuredFromTheJobsOwnSchedule:
    @pytest.mark.tripwire
    def test_a_normal_cadence_reports_no_missed_runs(self) -> None:
        """The majority path, and the control that stops this crying wolf. A sweep
        arriving on time must be silent, or the warning is noise."""
        gap, missed = health_sweep._sweep_gap(_job(_iso(300)))  # noqa: SLF001

        assert gap is not None and 290 <= gap <= 320
        assert missed == 0

    @pytest.mark.tripwire
    def test_one_late_run_is_jitter_not_an_outage(self) -> None:
        """A single missed tick is ordinary — a slow handler, a busy box. Warning on
        it would train the reader to ignore the line that matters."""
        _gap, missed = health_sweep._sweep_gap(_job(_iso(600)))  # noqa: SLF001

        assert missed <= 1

    @pytest.mark.tripwire
    def test_the_real_incident_is_loud(self) -> None:
        """4h05m at a 5-minute cadence — the actual 2026-09-07 window."""
        _gap, missed = health_sweep._sweep_gap(_job(_iso(4 * 3600 + 5 * 60)))  # noqa: SLF001

        assert missed >= 40, missed

    @pytest.mark.tripwire
    def test_the_interval_comes_from_the_scheduler_s_own_parser(self) -> None:
        """One source. A second copy of "how long is `every 5m`" is how the sweep and
        the scheduler come to disagree about the sweep's own cadence."""
        source = inspect.getsource(health_sweep)

        assert "parse_every" in source, "the cadence is derived somewhere else again"

    def test_an_unparseable_or_absent_schedule_says_nothing(self) -> None:
        """Degrade to silence, never to a false alarm: a cron-style or first-ever run
        has no interval to compare against and must not manufacture one."""
        assert health_sweep._sweep_gap(_job(None)) == (None, 0)  # noqa: SLF001
        assert health_sweep._sweep_gap(_job(_iso(9999), "0 3 * * *"))[1] == 0  # noqa: SLF001
        assert health_sweep._sweep_gap(_job("not-a-timestamp"))[1] == 0  # noqa: SLF001


class TestEveryExitPathCarriesIt:
    @pytest.mark.tripwire
    def test_the_gap_is_computed_inside_the_ONE_exit_helper(self) -> None:
        """There are four `_log_exit` call sites. Computing the gap at the call sites
        would be the actuator-on-some-paths shape this repo names first; computing it
        inside the helper covers all four by construction."""
        source = inspect.getsource(health_sweep._log_exit)  # noqa: SLF001

        assert "_sweep_gap" in source
        assert "since_previous_s" in source and "missed_runs" in source

    @pytest.mark.tripwire
    def test_a_blind_window_is_ANNOUNCED_not_just_recorded_as_a_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A field on an INFO line is countable afterwards; it is not a signal. Four
        hours of no monitoring deserves a line that says so in words.

        DRIVEN, NOT READ. The first version of this test asserted that the SOURCE
        contained "MONITORING WAS BLIND" and "log.scheduler.warning" — and a mutant
        that changed the guard to `if False:` SURVIVED it, because both strings are
        still in the source of a branch that can never run. That is this repo's own
        recorded lesson about vacuous guards, committed again while writing the guard
        against it. Only running the thing can tell a live branch from a dead one.
        """
        caplog.set_level("WARNING")
        health_sweep._log_exit(  # noqa: SLF001
            _job(_iso(4 * 3600)), verdict="all_healthy", total=15, duration_ms=1.0
        )

        assert any("MONITORING WAS BLIND" in r.message for r in caplog.records), (
            "a four-hour gap produced no warning — the announcement branch is dead"
        )
        assert all(
            r.levelname == "WARNING"
            for r in caplog.records
            if "MONITORING WAS BLIND" in r.message
        )

    @pytest.mark.tripwire
    def test_a_healthy_cadence_announces_NOTHING(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The control that makes the assertion above worth having, and it is not
        hypothetical: replayed over the 319 real inter-sweep gaps in the retained
        window, `missed >= 2` fires ONCE. 311 sit at zero missed and 7 at one."""
        caplog.set_level("WARNING")
        health_sweep._log_exit(  # noqa: SLF001
            _job(_iso(300)), verdict="all_healthy", total=15, duration_ms=1.0
        )

        assert not [r for r in caplog.records if "MONITORING WAS BLIND" in r.message]
