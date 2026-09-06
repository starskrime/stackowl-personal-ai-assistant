"""`busy_timeout` must be set BEFORE the pragma that needs to wait.

WHY THIS EXISTS — a measured production incident, 2026-09-03.

`_PRAGMAS` ran in this order:

    PRAGMA journal_mode=WAL          <- takes a database lock
    PRAGMA foreign_keys=ON
    PRAGMA busy_timeout=15000        <- the waiting policy, set THIRD
    PRAGMA auto_vacuum=INCREMENTAL

`journal_mode=WAL` acquires a lock. At that point `busy_timeout` is still its
default of **0**, so a contended open does not wait a single millisecond — it
raises `database is locked` immediately. The carefully-reasoned 15-second budget
three lines below was not yet in effect for the one statement that needed it.

WHAT IT COST, from the retained logs. Starting at 2026-09-03T09:24:43 and
decaying over two days: **35 `[db] pool._open_inside_lock: connect failed`**, 34
of them `sqlite3.OperationalError: database is locked` thrown at `pool.py:219`
(the pragma loop), and **49 `[loop] tick failed — the loop continues`** beginning
at the SAME SECOND. The ONE loop failed 39 times on 09-03 alone because it could
not open a connection.

PROVEN, and the FIRST measurement of it was WRONG — recorded because the wrong
one was more dramatic and would have shipped a false story. That probe passed
`sqlite3.connect(timeout=0)` and reported the current order failing "after
0.00s". `aiosqlite.connect()` passes no timeout, so the real default is **5
seconds**, and the 0.00s was the probe's own artifact.

The honest measurement, fresh database per attempt, a writer holding
`BEGIN EXCLUSIVE`:

  | writer holds | current order | busy_timeout first |
  |---|---|---|
  | **8s** | **FAILED after 5.01s** `database is locked` | **OK after 7.98s** |
  | 1s | OK after 0.94s | OK after 0.94s |

The failure lands at **5.01s — the DEFAULT**, which is the fingerprint: the
configured 15-second budget was never in effect for the pragma that needed it.
And the pragma block's own comment cites SkillsAssembly's **24-40s** catalogue
scan writing from the other process, which no 5-second budget survives. Under a
burst shorter than the default, the change does nothing — so it costs no
behaviour in the safe case.

THE ROOT CAUSE GENERALISES: a setting that governs WAITING was applied after the
operation that needed to wait. The pragma block reasons at length about the size
of the timeout and never about when it takes effect — and the reasoning it does
carry is about `execute()`/`fetch_all()` retries, which is a different code path
from connection setup entirely.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import _PRAGMAS


@pytest.mark.tripwire
def test_busy_timeout_is_the_FIRST_pragma() -> None:
    """The structural guarantee. Any pragma before it runs with a 0ms wait."""
    assert _PRAGMAS, "the pragma list is empty"
    assert "busy_timeout" in _PRAGMAS[0], (
        "busy_timeout must be the FIRST pragma — every pragma before it runs with "
        f"SQLite's default 0ms wait and fails instantly under contention: {_PRAGMAS}"
    )


@pytest.mark.tripwire
def test_the_lock_taking_pragma_comes_after_it() -> None:
    """Names the specific hazard rather than trusting the ordering rule alone:
    `journal_mode` is the one that takes a lock, so it is the one that must not
    precede the waiting policy."""
    wal = next((i for i, p in enumerate(_PRAGMAS) if "journal_mode" in p), None)
    timeout = next((i for i, p in enumerate(_PRAGMAS) if "busy_timeout" in p), None)

    assert wal is not None and timeout is not None, _PRAGMAS
    assert timeout < wal, (
        f"journal_mode (index {wal}) runs before busy_timeout (index {timeout}) — "
        "it takes a lock with no wait configured, which is the 2026-09-03 incident"
    )


# WHY THERE IS NO INTEGRATION TEST HERE, and it was written and then removed.
#
# A test that holds `BEGIN EXCLUSIVE` for longer than the 5s default and then
# opens a real `DbPool` reproduces the bug exactly — and HANGS the suite. This
# package's fixtures replay migrations against a database the holder has locked
# exclusively, so the two deadlock; it ran past 120s and was killed twice.
#
# A slow test that hangs is worse than no test, and the property that must not
# regress is not "SQLite waits" (that is SQLite's job, and it was measured
# directly) but "the waiting policy is configured before the statement that
# waits" — which is a fact about a list, checkable in microseconds by the two
# assertions above. The measurement lives in the docstring, where it is a
# record; the invariant lives in the tests, where it is a guard.
