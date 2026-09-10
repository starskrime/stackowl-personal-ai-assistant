"""The platform ran for 7h21m, served nothing, and every instrument said healthy.

DEBT-295. On 2026-09-08 a StackOwl process stayed alive for 7h21m and wrote not one
log record. `ps` found it, the socket was up, the last line read `orchestrator.run:
exit — ready`, and the tripwire gate was green. Nothing noticed, because every
liveness signal this platform owns is read INSIDE the core — `health_sweep` is itself
a job on the scheduler that had stopped turning, so the invigilator went down with the
thing it invigilates.

The one mechanism designed to be read from outside is `WatchdogService`'s `WATCHDOG=1`
ping, and it arms only when systemd exports `WATCHDOG_USEC`. MEASURED: 1,115 boots,
1,115 "systemd watchdog not configured — skipping", ZERO verdicts ever produced. The
liveness verdict function was written, wired and never once called.

These tests pin the two halves of the cure:

  * a contributor that answers "is this process still WORKING", not "can it reach its
    dependencies" — the four fail-open branches are the safety property, because this
    is the only `down` in the tree with a process kill behind it;
  * a supervisor OUTSIDE the process that consults it UNCONDITIONALLY. The
    unconditional part is the whole lesson: `WATCHDOG_USEC` was an env gate, no test
    asserted the loop existed, and 1,115 boots skipped it in silence.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sqlite3
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackowl.health.contributors import (
    SchedulerProgressContributor,
    default_stall_seconds,
)
from stackowl.scheduler.scheduler import longest_legitimate_silence_seconds
from stackowl.startup import orchestrator


def _make_jobs_db(tmp_path: Path, rows: list[tuple[str | None, int]]) -> Path:
    """A `jobs` table holding (last_run_at, enabled) — the two columns read."""
    db = tmp_path / "stackowl.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, last_run_at TEXT, "
        "enabled INTEGER NOT NULL DEFAULT 1)"
    )
    for i, (last_run_at, enabled) in enumerate(rows):
        conn.execute("INSERT INTO jobs VALUES (?,?,?)", (f"j{i}", last_run_at, enabled))
    conn.commit()
    conn.close()
    return db


def _iso(delta_s: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta_s)).isoformat()


class TestTheVerdictItself:
    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_a_scheduler_that_has_completed_nothing_for_hours_is_down(
        self, tmp_path: Path
    ) -> None:
        db = _make_jobs_db(tmp_path, [(_iso(-26_528), 1)])
        c = SchedulerProgressContributor(
            db, since=datetime.now(UTC) - timedelta(seconds=30_000)
        )
        status = await c.health_check()
        assert status.status == "down"
        assert status.remedy, "a down verdict with a kill behind it must say what to do"

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_a_scheduler_that_finished_a_job_a_minute_ago_is_ok(
        self, tmp_path: Path
    ) -> None:
        db = _make_jobs_db(tmp_path, [(_iso(-60), 1)])
        c = SchedulerProgressContributor(db, since=datetime.now(UTC) - timedelta(hours=2))
        assert (await c.health_check()).status == "ok"

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_the_largest_healthy_gap_ever_measured_is_not_a_stall(
        self, tmp_path: Path
    ) -> None:
        """711.7s — the third-largest of 22,919 real gaps, and the largest that was
        not an outage. Every gap above it in the corpus is one of the two recorded
        outages. If this ever goes red the threshold has crossed into the band the
        calibration proved empty."""
        db = _make_jobs_db(tmp_path, [(_iso(-712), 1)])
        c = SchedulerProgressContributor(db, since=datetime.now(UTC) - timedelta(hours=2))
        assert (await c.health_check()).status == "ok"


class TestTheFourFailOpenBranches:
    """"I could not measure it" must never render as "it has stopped" — this is the
    only `down` in the tree with a process kill behind it."""

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_an_install_with_no_enabled_job_is_never_stalled(
        self, tmp_path: Path
    ) -> None:
        """The restart bomb this design would otherwise have shipped: MAX() over an
        empty set is NULL forever, so every poll would read as an infinite stall."""
        db = _make_jobs_db(tmp_path, [(_iso(-99_999), 0)])
        c = SchedulerProgressContributor(db, since=datetime.now(UTC) - timedelta(days=2))
        status = await c.health_check()
        assert status.status == "degraded"

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_a_schedule_that_has_never_run_is_not_a_stall(
        self, tmp_path: Path
    ) -> None:
        db = _make_jobs_db(tmp_path, [(None, 1)])
        c = SchedulerProgressContributor(db, since=datetime.now(UTC) - timedelta(days=2))
        assert (await c.health_check()).status == "degraded"

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_an_unparseable_timestamp_is_not_evidence_of_a_stall(
        self, tmp_path: Path
    ) -> None:
        db = _make_jobs_db(tmp_path, [("not-a-timestamp", 1)])
        c = SchedulerProgressContributor(db, since=datetime.now(UTC) - timedelta(days=2))
        assert (await c.health_check()).status == "degraded"

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_a_database_it_cannot_read_is_not_evidence_of_a_stall(
        self, tmp_path: Path
    ) -> None:
        """A lock storm must not become a kill storm."""
        c = SchedulerProgressContributor(
            tmp_path / "does-not-exist.db", since=datetime.now(UTC) - timedelta(days=2)
        )
        status = await c.health_check()
        assert status.status == "degraded"

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_asking_about_a_missing_database_does_not_create_one(
        self, tmp_path: Path
    ) -> None:
        """A supervisor that creates the file it is asking about answers its own
        question wrongly on every fresh install."""
        missing = tmp_path / "absent.db"
        await SchedulerProgressContributor(
            missing, since=datetime.now(UTC)
        ).health_check()
        assert not missing.exists()

    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_a_platform_restarted_beside_an_old_hole_is_judged_on_what_it_has_done(
        self, tmp_path: Path
    ) -> None:
        """The exact shape of the 2026-09-08 recovery: the newest completion is eight
        hours old because the platform was DOWN. A supervisor that read that as a stall
        would kill the core it had just started, forever."""
        db = _make_jobs_db(tmp_path, [(_iso(-28_800), 1)])
        c = SchedulerProgressContributor(db, since=datetime.now(UTC))
        assert (await c.health_check()).status == "ok"


    @pytest.mark.tripwire
    @pytest.mark.asyncio
    async def test_a_naive_anchor_does_not_silently_disable_the_detector(
        self, tmp_path: Path
    ) -> None:
        """`max(aware, naive)` is a TypeError, and it would surface as "the probe
        raised" — fail-open — on every poll forever. The quietest way for a supervisor
        to die is to keep answering "still alive" because it cannot answer at all."""
        db = _make_jobs_db(tmp_path, [(_iso(-26_528), 1)])
        c = SchedulerProgressContributor(
            # naive UTC, not naive LOCAL — a local anchor would make this test's
            # verdict depend on the machine's offset, which is a flake, not a guard.
            db, since=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=9)
        )
        assert (await c.health_check()).status == "down"


class TestTheBudgetIsDerivedNotChosen:
    @pytest.mark.tripwire
    def test_the_budget_is_a_multiple_of_the_schedulers_own_patience(self) -> None:
        """One source. A literal downstream does not make the guard more patient when
        the handler timeout rises — it makes it start killing healthy processes."""
        assert default_stall_seconds() > longest_legitimate_silence_seconds()
        ratio = default_stall_seconds() / longest_legitimate_silence_seconds()
        assert ratio == pytest.approx(2.0)

    @pytest.mark.tripwire
    def test_the_budget_asks_the_scheduler_rather_than_restating_it(self) -> None:
        """The mutant this kills: `return 1800.0`. A number that happens to be right
        today and cannot follow the bound it is derived from."""
        src = inspect.getsource(default_stall_seconds)
        assert "longest_legitimate_silence_seconds" in src
        # `textwrap.dedent`, NOT `inspect.cleandoc`. cleandoc strips the common
        # indent of lines 2+, which for SOURCE is the function body — it turns a
        # readable function into an IndentationError. It is a docstring tool, and
        # this is the third time in this programme an AST guard has failed for its
        # own reasons and read exactly like the fix being missing.
        numbers = [
            n.value
            for n in ast.walk(ast.parse(textwrap.dedent(src)))
            if isinstance(n, ast.Constant) and isinstance(n.value, int | float)
        ]
        assert not numbers, f"the budget restates a literal: {numbers}"

    @pytest.mark.tripwire
    def test_the_budget_still_clears_the_shortest_recorded_outage(self) -> None:
        """15,146s — the 2026-09-07 four-hour database failure, the SHORTER of the two
        real outages. A budget above it detects neither."""
        assert default_stall_seconds() < 15_146.0


class _FakeProc:
    """A core that never exits on its own — the state `proc.wait()` cannot see."""

    def __init__(self) -> None:
        self.pid = 4242
        self.killed = False
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        return -9

    def kill(self) -> None:
        self.killed = True
        self._exited.set()


def _status(kind: str) -> object:
    from stackowl.health.status import HealthStatus

    return HealthStatus(
        name="scheduler_progress", status=kind, message="m", latency_ms=1.0, remedy="r"
    )


class TestTheSupervisorActsOnIt:
    @pytest.mark.asyncio
    async def test_a_core_that_stops_serving_is_killed_so_the_existing_respawn_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(orchestrator, "_STALL_POLL_SECONDS", 0.01)
        proc = _FakeProc()

        async def probe(_since: datetime) -> object:
            return _status("down")

        rc, was_stall, _ = await orchestrator._wait_for_exit_or_stall(
            proc, probe, datetime.now(UTC)  # type: ignore[arg-type]
        )
        assert proc.killed is True
        assert was_stall is True
        assert rc == -9

    @pytest.mark.asyncio
    async def test_a_degraded_verdict_never_kills(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(orchestrator, "_STALL_POLL_SECONDS", 0.01)
        proc = _FakeProc()

        async def probe(_since: datetime) -> object:
            return _status("degraded")

        task = asyncio.ensure_future(
            orchestrator._wait_for_exit_or_stall(proc, probe, datetime.now(UTC))  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.1)
        assert proc.killed is False
        task.cancel()

    @pytest.mark.asyncio
    async def test_a_probe_that_raises_never_kills(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken probe must not restart a healthy platform."""
        monkeypatch.setattr(orchestrator, "_STALL_POLL_SECONDS", 0.01)
        proc = _FakeProc()

        async def probe(_since: datetime) -> object:
            raise RuntimeError("probe is broken")

        task = asyncio.ensure_future(
            orchestrator._wait_for_exit_or_stall(proc, probe, datetime.now(UTC))  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.1)
        assert proc.killed is False
        task.cancel()

    @pytest.mark.asyncio
    async def test_a_core_that_exits_normally_is_not_reported_as_a_stall(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(orchestrator, "_STALL_POLL_SECONDS", 0.01)
        proc = _FakeProc()

        async def probe(_since: datetime) -> object:
            return _status("ok")

        task = asyncio.ensure_future(
            orchestrator._wait_for_exit_or_stall(proc, probe, datetime.now(UTC))  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.05)
        proc._exited.set()
        rc, was_stall, saw_progress = await task
        assert was_stall is False
        assert saw_progress is True
        assert proc.killed is False


class TestItIsArmedUNCONDITIONALLY:
    """`WATCHDOG_USEC` was an env gate; no test asserted the loop existed; 1,115 boots
    skipped it in silence. These are the assertions that absence needed."""

    @pytest.mark.tripwire
    def test_the_shared_liveness_recipe_includes_progress(self) -> None:
        src = inspect.getsource(orchestrator._build_liveness_aggregator)
        assert "SchedulerProgressContributor" in src, (
            "the systemd gate and the gateway must share ONE definition of alive"
        )

    @pytest.mark.tripwire
    def test_the_gateway_passes_a_stall_probe_with_no_flag_in_front_of_it(self) -> None:
        """The mutant this kills: gating the probe on an env var or a setting. A
        capability that ships OFF is decoration, and this one has 7h21m of evidence."""
        src = inspect.getsource(orchestrator.StartupOrchestrator)
        i = src.index("stall_probe=_probe_core_stall")
        window = src[max(0, i - 1200) : i]
        for gate in ("os.environ", "getenv", "if self._settings.", "enabled"):
            assert gate not in window.split("supervise_task")[-1], (
                f"the stall probe is gated on {gate!r} — it must ship armed"
            )

    @pytest.mark.tripwire
    def test_the_supervisor_defaults_to_watching_only_for_an_exit(self) -> None:
        """A vacuity control on the test above. `_supervise_core` keeps `stall_probe`
        optional so its many existing callers and tests are untouched — which means the
        assertion that the REAL wiring passes one is the only thing standing between
        this platform and another silent 7h21m."""
        sig = inspect.signature(orchestrator._supervise_core)
        assert sig.parameters["stall_probe"].default is None
