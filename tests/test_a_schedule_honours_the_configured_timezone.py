"""D18.5 — the daily schedule used the HOST's timezone, not the operator's.

`_next_local_hour_iso(hour)` in `scheduler/assembly.py` built the next local
HH:00 like this (it has since been DELETED — see below)::

    now = datetime.now()                                  # NAIVE — host local time
    candidate = now.replace(hour=hour, minute=0, ...)     # still naive
    return candidate.astimezone(UTC).isoformat()          # assumes the HOST zone

`.astimezone()` on a naive datetime assumes the machine's timezone. So a daily
check-in seeded at "09:00 local" meant 09:00 *on whatever box the platform runs
on*, never the `system.timezone` the operator configured.

MEASURED 2026-09-05 AND IT LOOKED FINE, which is the whole reason this test pins
the environment. On the development box the host zone is CDT and the configured
zone is `America/Chicago` — the same offset — so the function returned the right
answer **by coincidence**. Deploy the identical config to the ordinary
self-hosted shape, a server running UTC, and `_next_local_hour_iso(9)` returns
09:00Z, which is 04:00 for the operator. A five-hour error in the one feature
whose entire purpose is to arrive at a chosen hour.

THIS IS THE ONE CLAUSE OF THE REFERENCE'S TEST RUNNER THAT WAS NOT VACUOUS HERE.
Its CI-parity block pins `TZ=UTC`, and pinning TZ is what turns this from a latent
defect into a failing test: with the host zone forced to UTC the coincidence
disappears and the bug is visible. The credential-unsetting half of that same
block has nothing to bind to here (one harness-owned variable; every operator
secret is a `file:` reference under a home `conftest` already redirects), so this
is the clause worth adopting and it earned itself immediately.

THE FIX WAS TO DELETE IT, NOT TO TEACH IT. `compute_next_run` in
`scheduler_helpers.py` had always resolved `settings.system.timezone` for exactly
this computation — its docstring says the scheduler "shares the SAME tz the
quiet-hours clock uses" — so the seeding path held a SECOND implementation of one
rule, and the wrong copy decided every job's FIRST run while the right one decided
all the rest. `_first_run_for(schedule)` now passes the schedule STRING to that one
function, which also fixed a second defect the duplicate carried: it took an HOUR
only, so `daily@04:30` was seeded to fire at 04:00.

The test runs in a SUBPROCESS with `TZ=UTC` because the host timezone is the
thing under test: `time.tzset()` inside the session would leak into every later
test, and importing `datetime` once means an in-process change is not reliably
observed anyway.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_PROBE = """
import sys
import stackowl.config.settings as cfg

class _Sys:
    timezone = sys.argv[1]
class _S:
    system = _Sys()

cfg.cached_settings = lambda: _S()          # the operator's configured zone
from stackowl.scheduler.assembly import _first_run_for
print(_first_run_for("daily@%02d:00" % int(sys.argv[2])))
"""


def _run(tz_env: str, configured: str, hour: int) -> str:
    env = dict(os.environ, TZ=tz_env)
    env.pop("STACKOWL_CONFIG_FILE", None)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE, configured, str(hour)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr[-500:]}"
    return result.stdout.strip()


@pytest.mark.tripwire
def test_the_host_timezone_does_not_decide_when_a_job_runs() -> None:
    """The same config must seed the same UTC instant on any machine.

    This is the assertion the defect fails: run the identical configured zone
    under two different HOST zones and the answer must not move.
    """
    on_utc_box = _run("UTC", "America/Chicago", 9)
    on_tokyo_box = _run("Asia/Tokyo", "America/Chicago", 9)

    assert on_utc_box == on_tokyo_box, (
        f"the host timezone changed the schedule: UTC box gave {on_utc_box}, "
        f"Tokyo box gave {on_tokyo_box}. `_next_local_hour_iso` must resolve "
        "`system.timezone`, not the machine's."
    )


@pytest.mark.tripwire
def test_nine_am_chicago_is_not_nine_am_utc() -> None:
    """The concrete failure, stated as the operator would experience it.

    On a UTC server the old code returned 09:00Z for a 09:00 America/Chicago
    check-in — 04:00 for the operator. The correct answer is 14:00Z or 15:00Z
    depending on daylight saving, and never 09:00Z.
    """
    result = _run("UTC", "America/Chicago", 9)
    hour_utc = int(result.split("T")[1][:2])

    assert hour_utc != 9, (
        f"a 09:00 America/Chicago schedule resolved to {result} — the host's "
        "timezone was used instead of the configured one"
    )
    assert hour_utc in (14, 15), (
        f"09:00 America/Chicago should be 14:00Z (CDT) or 15:00Z (CST); got {result}"
    )


def test_a_utc_operator_still_gets_utc() -> None:
    """The control. A zero-offset configured zone must be unaffected, or the
    fix would just be a different constant error."""
    result = _run("Asia/Tokyo", "UTC", 9)
    assert int(result.split("T")[1][:2]) == 9, result
