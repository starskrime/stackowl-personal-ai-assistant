"""A periodic reminder to do the work that never competes well with the answer.

WHY THIS IS A PRIMITIVE AND NOT A ONE-OFF. Measured 2026-09-02 across five days
of logs, the memory nudge fired **96 times** and **27 of those (28%) were followed
by a memory write within ten minutes** — and 27 of the 35 writes in that whole
window, **77%**, followed a nudge. Without it, "when the agent decides to" measured
across a real week is close to zero.

THAT REFUTES THE OBVIOUS READING OF D09.1. That item measured `evolve_now` at ZERO
invocations all-time and `synthesize_skills` at zero, against `note_applied_lesson`
at 791, and concluded that tools asking the model to stop and do meta-work are
never chosen. True — but the conclusion "so prompting cannot help" does not
follow. The model will not VOLUNTEER meta-work; when the platform ASKS at the
right moment it complies about a quarter of the time, and that quarter is where
almost all of the writes come from.

ONE COUNTER, NOT TWO. This was written inline in ``memory/curated.py`` and the
skill nudge (D09.4) needs exactly the same mechanism with different words. Two
copies of a counter is the shape this codebase keeps paying for, so the mechanism
lives here and each caller supplies its own interval, text and log label.

IN-PROCESS, DELIBERATELY. The counter resets on restart, and 2026-08-17 measured
how far wrong that goes: at 34 boots in a day the reset is the dominant term, not
a rounding error. The intervals absorb it. Persisting a counter whose only job is
to fire "occasionally" would buy accuracy nobody needs — see D08.3 for the option
and why it was not taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.infra.observability import log


@dataclass
class TurnNudge:
    """Counts turns per lane and yields its text when one is due.

    Attributes:
        interval: Turns between nudges. Never below 1.
        text: What the model is told. Rides the VOLATILE per-turn context, never
            the frozen system prompt — a nudge in the prompt would be present for
            an entire conversation or absent from all of it.
        label: The log line's identity, e.g. ``"[curated] nudge"``.
    """

    interval: int
    text: str
    label: str
    _seen: dict[str, int] = field(default_factory=dict, init=False)

    def note_turn(self, lane: str) -> str | None:
        """Count a turn on *lane*; return the text when a nudge is due.

        Returns ``None`` almost always. Never raises — a reminder may not cost a
        turn its answer.
        """
        try:
            key = lane or ""
            count = self._seen.get(key, 0) + 1
            if count < max(1, self.interval):
                self._seen[key] = count
                return None
            self._seen[key] = 0
            log.memory.info(
                f"{self.label}: due",
                extra={"_fields": {"session_key": key, "turns": count}},
            )
            return self.text
        except Exception as exc:  # noqa: BLE001 — never cost the turn
            log.memory.warning(
                f"{self.label}: counter failed — no nudge this turn",
                exc_info=exc, extra={"_fields": {"lane": lane}},
            )
            return None

    def note_action(self, lane: str) -> None:
        """Reset the lane — the agent just did the thing, so it needs no hint."""
        try:
            self._seen[lane or ""] = 0
        except Exception as exc:  # noqa: BLE001
            log.memory.warning(
                f"{self.label}: reset failed", exc_info=exc,
                extra={"_fields": {"lane": lane}},
            )

    def reset(self) -> None:
        """Clear every lane. For tests, and for a deliberate restart."""
        self._seen.clear()
