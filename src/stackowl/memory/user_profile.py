"""The stable user profile loaded into every system prompt (D01.1).

Bakir's Q5/Q12/Q13, reconciled: a stable picture of the user, always loaded,
PLUS a `memory` tool the model calls when a conversation needs depth. Two of his
answers could not both hold as stated — "a stable picture of me" and "I won't
give up per-message search" — and surfacing that contradiction is what produced
this shape, which neither answer contained on its own.

Why a plain file the user can open and edit (Q6): they can see exactly what
their assistant is being told about them, and correct it directly. It is
returned VERBATIM for that reason — reformatting would quietly break that
contract.

Why it matters mechanically: per-turn memory recall varied in EVERY session
measured (2026-07-27), making it the single largest source of system-prompt
instability, and an unstable prompt forfeits the provider's automatic prefix
cache silently — no marker to blame, no error to notice.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

#: Default location. Deliberately alongside the user's other state rather than
#: in the project directory — all runtime state lives under ~/.stackowl/.
PROFILE_FILENAME = "USER.md"


def default_profile_path() -> Path:
    """Where the profile lives unless configured otherwise."""
    return StackowlHome.home() / PROFILE_FILENAME


def load_user_profile(path: Path | None = None) -> str:
    """The user's profile text, or ``""`` when there isn't one.

    Never raises (invariant I2): a profile that cannot be read degrades to its
    absence and is logged, because a missing file must cost the user some
    context, never their reply. Most installs will not have written one yet, so
    absence is the ORDINARY case and is logged at debug rather than error — only
    a file that exists and cannot be read is worth shouting about.

    A whitespace-only file counts as absent: injecting a blank region would be
    an invisible difference between installs that have touched the file and
    those that have not.
    """
    target = path or default_profile_path()
    try:
        if not target.is_file():
            log.memory.debug(
                "[memory] user_profile: no profile file — continuing without one",
                extra={"_fields": {"path": str(target)}},
            )
            return ""
        text = target.read_text(encoding="utf-8").strip()
    except Exception as exc:
        log.memory.error(
            "[memory] user_profile: profile unreadable — continuing without it",
            exc_info=exc,
            extra={"_fields": {"path": str(target)}},
        )
        return ""
    log.memory.debug(
        "[memory] user_profile: loaded",
        extra={"_fields": {"path": str(target), "chars": len(text)}},
    )
    return text
