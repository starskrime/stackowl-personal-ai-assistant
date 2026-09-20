"""The undo-eligibility gate -- FR88, Story 4.5, AD-26/AD-27.

FR88, verbatim: "Undo stays available on an action's card until the action is
superseded (a later change to the same target) or 24 hours pass, whichever
comes first; after that the card no longer offers undo and an undo request is
refused."

PURE, DELIBERATELY -- same rationale as ``action_policy.py``: by the time
``commands/spec/undo.py::request_undo`` calls :func:`decide_undo`, it already
knows both facts this gate needs (how long ago the original command
completed, and whether a later command touched the same target) -- gathering
those two facts is I/O and lives in ``pipeline/durable/store.py``, never
here. A gate that reads anything external cannot be unit-tested as a plain
decision table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from stackowl.config.journal_settings import JournalSettings
from stackowl.infra.observability import log

#: FR88, verbatim: "24 hours pass". Not operator-configurable -- the
#: architecture states this as a fixed window (ARCHITECTURE-SPINE.md's own
#: FR88 resolution), unlike ``TaskLoopSettings.prune_completed_after_days``,
#: which IS a tunable.
UNDO_WINDOW = timedelta(hours=24)

#: ``UNDO_WINDOW`` expressed in the day-granularity ``prune_completed`` (and
#: its ``older_than_days``/``command_older_than_days`` parameters) already
#: use -- derived, not hand-typed, so the two can never silently disagree.
UNDO_WINDOW_DAYS = math.ceil(UNDO_WINDOW.total_seconds() / 86400)

#: NFR45, verbatim: "completed COMMAND rows [are kept] at least as long as
#: the undo window ... and journal retention." A completed COMMAND row's
#: prune floor is therefore the LONGER of the two. Derived from
#: ``JournalSettings()``'s DEFAULT instance -- mirrors ``journal/
#: retention.py::JOURNAL_RETENTION_DAYS``'s own "single source of truth, not
#: a hand-typed copy" rationale, so this constant tracks that default if it
#: ever changes rather than drifting from it. The one production caller
#: (``startup/orchestrator.py``) passes the LIVE, possibly operator-tuned
#: ``Settings().journal.retention_days`` instead of this constant --
#: ``pipeline/durable/store.py::prune_completed``'s own
#: ``command_older_than_days`` default exists only as a safe fallback for a
#: caller that does not wire one in.
COMMAND_PRUNE_FLOOR_DAYS = max(UNDO_WINDOW_DAYS, JournalSettings().retention_days)

UndoRefusalCode = Literal["superseded", "expired"]


@dataclass(frozen=True)
class UndoDecision:
    """What the gate decided. ``code``/``reason``/``remedy`` are set only
    when ``allowed`` is ``False`` -- the AC's own literal shape ("the gate
    refuses it with ``{code, reason, remedy}``", Story 4.5 AC3)."""

    allowed: bool
    code: UndoRefusalCode | None = None
    reason: str | None = None
    remedy: str | None = None


def decide_undo(*, elapsed: timedelta, superseded: bool) -> UndoDecision:
    """FR88's own two refusal conditions. ``superseded`` is checked FIRST --
    a superseded command is refused even well inside the 24h window, since a
    later change already overwrote what undo would restore."""
    # 1. ENTRY
    log.engine.debug(
        "[authz] undo.decide_undo: entry",
        extra={"_fields": {
            "elapsed_seconds": elapsed.total_seconds(), "superseded": superseded,
        }},
    )
    # 2. DECISION -- superseded outranks elapsed time (see docstring above).
    decision: UndoDecision
    if superseded:
        decision = UndoDecision(
            allowed=False,
            code="superseded",
            reason="a later command already changed this target",
            remedy="undo is no longer available -- issue a new command instead",
        )
    elif elapsed >= UNDO_WINDOW:
        decision = UndoDecision(
            allowed=False,
            code="expired",
            reason="the 24-hour undo window has passed",
            remedy="undo is no longer available -- issue a new command instead",
        )
    else:
        decision = UndoDecision(allowed=True)
    # 4. EXIT
    log.engine.debug(
        "[authz] undo.decide_undo: exit",
        extra={"_fields": {"allowed": decision.allowed, "code": decision.code}},
    )
    return decision
