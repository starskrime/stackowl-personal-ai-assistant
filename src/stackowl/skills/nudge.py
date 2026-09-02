"""D09.4 — periodically ask whether this work is worth a skill.

WHY, WITH THE NUMBER THAT DECIDED IT. The obvious reading of D09.1 is that the
model never does meta-work: `evolve_now` and `synthesize_skills` have ZERO
invocations all-time, against `note_applied_lesson`'s 791. That reading says a
nudge cannot help.

MEASURED 2026-09-02 across five days of logs, it is wrong. The MEMORY nudge — the
same mechanism, different words — fired 96 times and 27 of those (28%) were
followed by a memory write within ten minutes. More decisively, **27 of the 35
writes in that window, 77%, followed a nudge.** The model will not VOLUNTEER
meta-work; asked at the right moment it complies about a quarter of the time, and
that quarter is where almost all of the writes come from.

GATED ON THE TOOL BEING PRESENT, which is the reference platform's rule and the
one this project has already paid to learn. `skills_list` showed zero invocations
for eight days, and the cause was not that the model ignored it — on those turns
it was NOT IN THE PRESENTED SET to call. Nudging toward a tool the model cannot
reach would manufacture exactly that failure on purpose.

FAILS CLOSED. An unknown presented set means no nudge: telling the model to use a
tool that may not be there is worse than staying quiet.
"""

from __future__ import annotations

from stackowl.infra.nudge import TurnNudge

#: The tool this nudge points at. If it is not presented, the nudge is silent.
SKILL_TOOL = "skill_manage"

#: Turns between skill nudges. Deliberately LONGER than the memory nudge's 4:
#: a durable fact appears in most conversations, a reusable procedure does not,
#: and a nudge that fires when there is nothing to record trains the model to
#: ignore it. The reference platform uses 10 for the same reason.
SKILL_NUDGE_INTERVAL_TURNS = 10

_TEXT = (
    "If the work in this conversation followed a repeatable procedure — a "
    "sequence of steps that would work again on a similar request — record it as "
    "a skill with skill_manage now. Only if it would genuinely be reused; a skill "
    "that describes one specific request is worse than none."
)

_SKILL_NUDGE = TurnNudge(
    interval=SKILL_NUDGE_INTERVAL_TURNS, text=_TEXT, label="[skills] nudge",
)


def note_turn(lane: str, presented_tools: frozenset[str] | None) -> str | None:
    """Count a turn; return the nudge when one is due AND the tool is reachable.

    Args:
        lane: The conversation lane to count against.
        presented_tools: Names the model can actually call this turn. ``None``
            means unknown, and yields no nudge — see the module docstring.

    Returns:
        The nudge text, or ``None``. Never raises.
    """
    if presented_tools is None or SKILL_TOOL not in presented_tools:
        return None
    return _SKILL_NUDGE.note_turn(lane)


def note_skill_written(lane: str) -> None:
    """Reset the lane — a skill was just recorded, so it needs no hint."""
    _SKILL_NUDGE.note_action(lane)


def reset() -> None:
    """Clear every lane. For tests."""
    _SKILL_NUDGE.reset()
