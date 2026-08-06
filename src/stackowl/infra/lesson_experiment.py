"""Is lesson injection actually helping? (ADR-19 intervention #4)

Lives in ``infra`` rather than ``pipeline`` for the reason
``infra/prompt_metrics.py`` spells out: the decision is made pipeline-side but
the label is recorded in ``memory/outcome_store``, and memory must not import
pipeline. A ContextVar in ``infra`` is the seam both may use.

WHAT WAS MEASURED, 2026-08-05. Lessons ARE fed back — `classify.py` calls
`_gather_lessons` unconditionally on every turn, and the selector is
deliberately query-independent so the block stays byte-stable across a session
(D01.1's prompt-cache work). 2,680 lessons are stored. So the loop's FEEDBACK leg
is closed and closed well.

What is missing is ADR-19 obligation ③, VERIFY: **nothing measures whether
injecting them changes anything.** The only signal that a lesson mattered is
``note_applied_lesson``, a tool the model must volunteer to call — and it called
it ONCE in fifteen days across 2,193 turns.

So we pay prompt tokens on every turn for a mechanism whose effect has never been
observed. Not because it fails — because nothing looks.

THE EXPERIMENT. Hold the lessons block out of a fraction of turns and compare
``task_outcomes.quality_score`` between the two arms. The platform already
records that score for ~400 successful turns a week, so the substrate exists; all
that was missing was a label saying which arm each turn was in.

WHY THE ARM IS KEYED ON THE SESSION, NOT THE TURN. This is the load-bearing
design decision. Withholding the block changes the system prompt, and D01.1 exists
precisely because a prompt that changes mid-conversation destroys the
per-conversation cache (Law 1). A per-TURN coin flip would reintroduce exactly
that defect in the name of measuring a different one. Keyed on ``session_key``,
every turn of a conversation gets the same arm, the prompt stays byte-stable
within the session, and the experiment is invisible to the cache.

HONEST COST. If lessons help, held-out sessions get slightly worse answers. That
is the price of finding out, and it is bounded by the hold-out rate and instantly
reversible by setting it to zero. The alternative — keep paying on 100% of turns
forever without evidence — is the more expensive option, just less visibly.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar

from stackowl.infra.observability import log

__all__ = [
    "ARM_HELD_OUT",
    "ARM_INJECTED",
    "arm_for_session",
    "current_arm",
    "set_arm",
]

#: Lessons were assembled into the prompt for this turn (the control).
ARM_INJECTED = "injected"
#: Lessons were deliberately withheld so the difference can be measured.
ARM_HELD_OUT = "held_out"

#: Fraction of SESSIONS held out, as a percentage. Ships ON — a dormant
#: experiment answers nothing, and this programme's rule is that a finished
#: feature ships on rather than waiting behind a flag.
#:
#: 20 rather than 50: at ~400 scored turns/week that is ~80 held-out turns a
#: week, enough to see a real effect within a couple of weeks, while four in
#: five sessions keep the (possible) benefit. Set to 0 to end the experiment;
#: every turn then reports ``injected`` and behaviour is exactly as before.
HOLD_OUT_PERCENT = 20

_arm: ContextVar[str] = ContextVar("lesson_arm", default=ARM_INJECTED)


def arm_for_session(session_key: str | None, *, hold_out_percent: int = HOLD_OUT_PERCENT) -> str:
    """Which arm this SESSION is in. Deterministic, so a session never flips.

    Hashed rather than random: the same session must resolve to the same arm on
    every turn, across restarts and across processes (the gateway and core are
    separate processes and both assemble prompts). A random draw would give a
    conversation a different prompt on its second turn — the precise D01.1
    failure this experiment must not cause.

    An absent session_key falls back to the control. Background and utility
    calls have no conversation to hold out, and quietly degrading them would
    contaminate the comparison with turns that were never comparable.
    """
    if hold_out_percent <= 0 or not session_key:
        return ARM_INJECTED
    if hold_out_percent >= 100:
        return ARM_HELD_OUT
    digest = hashlib.sha256(session_key.encode("utf-8")).digest()
    # First two bytes → 0..65535, scaled to 0..99. Using the raw digest rather
    # than Python's hash(): the latter is salted per process, so the gateway and
    # core would disagree about the same session.
    bucket = ((digest[0] << 8) | digest[1]) % 100
    return ARM_HELD_OUT if bucket < hold_out_percent else ARM_INJECTED


def set_arm(arm: str) -> None:
    """Record this turn's arm for the outcome recorder to read."""
    _arm.set(arm)


def current_arm() -> str:
    """This turn's arm, defaulting to the control."""
    return _arm.get()


def resolve_and_record(session_key: str | None) -> str:
    """Decide the arm for this turn, stash it, and log the withholding.

    The hold-out is logged at INFO rather than debug: an experiment that
    silently degrades a fraction of traffic is indistinguishable from a bug, and
    ADR-19 I6 exists because of exactly that confusion.
    """
    arm = arm_for_session(session_key)
    set_arm(arm)
    if arm == ARM_HELD_OUT:
        log.engine.info(
            "[lessons] holding lessons out of this session — measuring whether "
            "injection helps (ADR-19 #4)",
            extra={"_fields": {
                "session_key": session_key,
                "hold_out_percent": HOLD_OUT_PERCENT,
            }},
        )
    return arm
