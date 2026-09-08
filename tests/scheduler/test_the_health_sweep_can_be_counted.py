"""The platform's own health signal had no denominator, and it cost a wrong number.

MEASURED 2026-09-08 across the whole retained window (11 days):

    2705  health sweeps actually dispatched   (the scheduler's wrapper exit line)
     819  health_sweep.execute: UNHEALTHY subsystems detected   ERROR   — visible
      50  health_sweep.execute: subsystems RECOVERED after heal WARNING — visible
       0  health_sweep.execute: all healthy                     DEBUG   — INVISIBLE

Production runs at INFO. So the one outcome that says the platform is FINE is the
one outcome that never reaches the log, and roughly 1,836 healthy sweeps left no
trace at all. Every failure is visible and no success is, which makes "819
unhealthy" unreadable: it could be 819 of 819 or 819 of 2,705, and the logs cannot
tell you which. That is CLAUDE.md's most-repeated rule — check what a denominator
is MADE of — broken by the health subsystem itself.

IT IS NOT A STYLE POINT; IT PRODUCED A FALSE FINDING. Investigating the 2026-09-07
outage this loop reported "`health_sweep.execute: exit` fired ZERO times against
~49 expected" and offered it as verified evidence that the monitor died with the
database. There is no `execute: exit` line in this handler AT ALL — `entry` is
DEBUG, `all healthy` is DEBUG, and the only INFO bracket belongs to the scheduler's
job wrapper, which knows nothing about the verdict. The zero was a string that
cannot appear, which is exactly the D14.4 defect this programme already recorded,
committed a second time against the same subsystem.

(The underlying concern survived re-measurement on the right lines — 1 job
completion in the 4h05m window against ~450-540 per comparable window — but the
number offered as proof was from an instrument that does not exist.)

THE FIX IS THE 4-POINT RULE, honestly applied: `execute()` gets an INFO exit on
EVERY return path, carrying the verdict as a FIELD. A field can be grouped; a
message identity cannot, and splitting the verdict across three differently-levelled
messages is what removed the denominator in the first place.

`entry` deliberately stays DEBUG: the scheduler already logs
`[scheduler] health_sweep-<id>: entry` at INFO for every dispatch, so a second
entry line would be a duplicate. The EXIT is not a duplicate — the wrapper's exit
says the handler returned, this one says what it concluded.
"""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap

import pytest

from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers import health_sweep as mod
from stackowl.scheduler.handlers.health_sweep import HealthSweepHandler
from stackowl.scheduler.job import Job

_EXIT_MSG = "[scheduler] health_sweep.execute: exit"


def _job() -> Job:
    """A real Job row — `Job` is frozen with `extra="forbid"`, so a guessed field
    set raises rather than quietly standing in for the real thing."""
    return Job(
        job_id="j1", handler_name="health_sweep", schedule="*/5 * * * *",
        idempotency_key="k1", last_run_at=None, next_run_at="2026-09-08T00:00:00Z",
        status="running",
    )


class _Aggregator:
    def __init__(self, statuses: list[HealthStatus] | Exception) -> None:
        self._statuses = statuses

    async def collect(self) -> list[HealthStatus]:
        if isinstance(self._statuses, Exception):
            raise self._statuses
        return self._statuses


def _ok(name: str = "db") -> HealthStatus:
    return HealthStatus(name=name, status="ok", message="", latency_ms=1.0)


def _down(name: str = "db") -> HealthStatus:
    return HealthStatus(name=name, status="down", message="gone", latency_ms=1.0)


def _execute_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(textwrap.dedent(inspect.getsource(HealthSweepHandler.execute)))
    fn = tree.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    return fn


def test_every_return_path_logs_the_exit_and_none_is_missed() -> None:
    """AN ACTUATOR ON ONLY SOME PATHS IS THIS REPO'S FAILURE MODE #1.

    A denominator that counts three of four outcomes is worse than none, because
    it looks like a denominator. This walks the AST rather than the text so a
    fourth `return JobResult(...)` added later cannot quietly skip the line.
    """
    fn = _execute_fn()
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert len(returns) >= 4, (
        f"expected the four known outcomes (aggregator-raised, healthy, recovered, "
        f"unhealthy); found {len(returns)} returns"
    )

    # Count CALLS to the shared emitter, not the message string: the string lives
    # in `_log_exit` precisely so the four paths cannot drift into four wordings.
    exits = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_log_exit"
    ]
    assert len(exits) == len(returns), (
        f"{len(returns)} return paths but {len(exits)} `_log_exit` calls — a sweep "
        f"that returns without logging its verdict is invisible in production, "
        f"which is the defect this file exists to prevent"
    )


def test_the_exit_line_is_INFO_because_production_does_not_record_DEBUG() -> None:
    """The whole defect in one assertion.

    `all healthy` was DEBUG and therefore absent from 2,705 real sweeps. An exit
    line at DEBUG would reproduce that exactly, so the level is pinned rather
    than left to review.
    """
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    levels: set[str] = set()
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        if not isinstance(call.func, ast.Attribute):
            continue
        for arg in call.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.startswith(_EXIT_MSG)
            ):
                levels.add(call.func.attr)
    assert levels, "no logging call emits the exit line"
    assert levels == {"info"}, (
        f"the exit line must be INFO; found {sorted(levels)}. Production runs at "
        f"INFO, so a DEBUG verdict does not exist when someone needs it."
    )


@pytest.mark.asyncio
async def test_a_healthy_sweep_leaves_a_countable_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The 1,836 sweeps that used to vanish. Verdict rides as a FIELD, not as a
    distinct message, so the outcomes can be grouped into a ratio."""
    handler = HealthSweepHandler(_Aggregator([_ok(), _ok("cache")]))

    with caplog.at_level(logging.INFO, logger="stackowl.scheduler"):
        result = await handler.execute(_job())

    assert result.success is True
    lines = [r for r in caplog.records if r.getMessage().startswith(_EXIT_MSG)]
    assert len(lines) == 1, "exactly one exit line per sweep, or it is not a count"
    fields = getattr(lines[0], "_fields", {})
    assert fields.get("verdict") == "healthy"
    assert fields.get("total") == 2


@pytest.mark.asyncio
async def test_an_unhealthy_sweep_is_counted_by_the_same_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both outcomes on ONE line is the point — the ratio is unreadable when the
    numerator and the denominator are different messages at different levels."""
    handler = HealthSweepHandler(_Aggregator([_down(), _ok("cache")]))

    with caplog.at_level(logging.INFO, logger="stackowl.scheduler"):
        await handler.execute(_job())

    lines = [r for r in caplog.records if r.getMessage().startswith(_EXIT_MSG)]
    assert len(lines) == 1
    assert getattr(lines[0], "_fields", {}).get("verdict") == "unhealthy"


@pytest.mark.asyncio
async def test_even_a_probe_that_raises_is_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The path most likely to be missed, and the one an outage actually takes."""
    handler = HealthSweepHandler(_Aggregator(RuntimeError("probe exploded")))

    with caplog.at_level(logging.INFO, logger="stackowl.scheduler"):
        result = await handler.execute(_job())

    assert result.success is False
    lines = [r for r in caplog.records if r.getMessage().startswith(_EXIT_MSG)]
    assert len(lines) == 1
    assert getattr(lines[0], "_fields", {}).get("verdict") == "probe_failed"
