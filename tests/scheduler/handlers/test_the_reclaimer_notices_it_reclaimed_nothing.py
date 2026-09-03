"""A reclaimer that asks for 2,000 pages and gets 1 must say so.

MEASURED 2026-09-03 on the live database, across 146 consecutive hourly ticks:

    reclaimed_pages: 1      every tick
    free_ratio:      0.22   unchanged for 8+ hours
    file_mb:         359.4  unchanged

19,285 of 87,749 pages — 79 MB, 22% of the file — sat on the freelist while the
handler asked for 2,000 a pass and got one. Reproduced on a COPY of the live
database: ``PRAGMA incremental_vacuum(N)`` returns exactly one page per call
whatever N is, whatever the argument form, stepped or not. A full ``VACUUM`` on
that same copy took it from 359.4 MB to 277.3 MB — 82 MB the incremental path
cannot reach.

WHY NOTHING NOTICED, WHICH IS THE ACTUAL DEFECT. This handler has two
self-checks and BOTH ask about configuration rather than effect:

* ``needs_one_time_vacuum()`` asks ``PRAGMA auto_vacuum == 0``. The live database
  reports 2 (INCREMENTAL), so it answers "fine" — it is reporting the MODE while
  the failure is in the RESULT.
* the "falling behind" alarm fires on ``free_ratio > 0.25``. The stuck value is
  **0.22**. The alarm sits three points above the failure and has never fired in
  146 ticks.

And the handler ALREADY COMPUTES the number that proves it: ``reclaimed =
pages_before - pages_after``. It logs it at INFO every tick. Nothing compares it
to what was asked for. Its own docstring says "a reclaimer that silently falls
behind looks exactly like one that is working" — and then it watches a ratio
instead of its own result.

THE PREDICATE NEEDS NO MAGIC NUMBER. If the freelist held at least as many pages
as the pass requested, and the pass did not get them, reclaim is not working.
That is true whatever the cause — a database that cannot convert, a mode that was
never applied, or a future SQLite that changes its mind about what N means.

Note the existing sibling test builds its fixture with ``PRAGMA auto_vacuum=2``
BEFORE creating any table, so its database is BORN incremental and reclaims
normally. It passes, and it cannot show this bug — which is why the guard here is
on the decision, not on a synthesised file.
"""

from __future__ import annotations

import pytest

from stackowl.scheduler.handlers.db_reclaim import reclaim_stalled


def test_the_live_shape_is_reported_as_stalled() -> None:
    """The exact measured numbers: asked 2,000, got 1, 19,285 still free."""
    assert reclaim_stalled(asked=2000, reclaimed=1, free_after=19_285) is True


def test_a_pass_that_got_what_it_asked_for_is_not_stalled() -> None:
    assert reclaim_stalled(asked=2000, reclaimed=2000, free_after=5000) is False


def test_an_almost_empty_freelist_is_not_stalled() -> None:
    """The healthy steady state: little to reclaim, so a small reclaim is right.

    This is the case that must NEVER warn, or the alarm becomes noise and gets
    ignored — which is how the free ratio alarm died: it was set where it would
    not fire rather than where the failure is."""
    assert reclaim_stalled(asked=2000, reclaimed=3, free_after=3) is False
    assert reclaim_stalled(asked=2000, reclaimed=0, free_after=0) is False


def test_a_freelist_exactly_the_size_of_the_request_still_counts() -> None:
    """Boundary: the freelist could have satisfied the request in full."""
    assert reclaim_stalled(asked=2000, reclaimed=1, free_after=2000) is True


def test_a_partial_reclaim_with_plenty_left_is_stalled() -> None:
    """Getting SOME pages is not proof of health when the backlog dwarfs the ask
    — 146 ticks of "1" is a partial reclaim too."""
    assert reclaim_stalled(asked=2000, reclaimed=900, free_after=19_285) is True


@pytest.mark.parametrize("asked", [0, -1])
def test_a_pass_that_asked_for_nothing_is_never_stalled(asked: int) -> None:
    """max_pages is operator-settable via job params; a zero or nonsense request
    must not manufacture an alarm."""
    assert reclaim_stalled(asked=asked, reclaimed=0, free_after=19_285) is False
