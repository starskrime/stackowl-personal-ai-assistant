"""Provisional journal retention constant (Story 2.6).

The epic's own Technical Decisions state that concrete retention values "stay
provisional until Story 2.12's benchmark." This module exists ONLY so Story
2.6's own AD-4/NFR45 tripwire (``tests/journal/test_retention_tripwire.py``)
has a value to check subsystem prune windows against -- it is NOT the real
journal retention setting AD-6 describes (a 30-day default, a setting, pruned
by one seeded job). Building that setting and its prune job is explicitly
Story 2.11/2.12's scope; this module owns nothing beyond the one constant.

WHY 1 DAY, NOT THE ARCHITECTURE'S EVENTUAL ~30-DAY DEFAULT: today's REAL
production prune windows are ``prune_completed_after_days=1``
(``config/task_loop_settings.py``, tasks, owner-authorized 2026-09) and
``_RUN_HISTORY_RETENTION_DAYS=7`` (``scheduler/handlers/db_reclaim.py``,
job_runs, owner-authorized 2026-09-02) -- both far below 30. A tripwire
compared against 30 would fail immediately against values this story does not
own and has no basis to change unilaterally. Setting this constant to the
tightest existing real window (1 day) makes the check HONEST -- it genuinely
fails on a real regression, e.g. a future 0-day window -- without forcing a
change to owner-authorized production behaviour. Story 2.11 raises this to the
real setting (architecturally ~30 days) and must simultaneously reconcile
every subsystem prune window this tripwire checks -- logged to
``_bmad-output/implementation-artifacts/deferred-work.md``.
"""

from __future__ import annotations

#: Matches ``task_loop_settings.py::prune_completed_after_days``'s current
#: value -- the tightest real production prune window today. See the module
#: docstring above for why. Used ONLY by this story's own tripwire test; no
#: production prune job reads this constant.
PROVISIONAL_JOURNAL_RETENTION_DAYS = 1
