"""Shared per-step error format contract (REACT-7 / F092).

The pipeline backends self-heal a step exception by appending a human-readable
marker to ``state.errors`` AND a structured :class:`StepError` record. The
critical-failure / honesty surfaces must NOT re-parse a free-form string (a drift
in one writer would silently break detection). This module is the SINGLE place the
human string is formatted and parsed, so both the writer and the reader change
together if the format ever moves.

Canonical string format: ``"<step>: <ExcType>: <message>"`` (unchanged from the
original asyncio/langgraph backends — kept so ``classify_failure`` and existing
status logic stay byte-compatible). Structured records are the PRIMARY signal; the
parser is the back-compat fallback for any error string written outside this seam.
"""

from __future__ import annotations


def format_step_error(step: str, exc: BaseException) -> str:
    """Render the canonical ``"<step>: <ExcType>: <message>"`` marker for an error."""
    return f"{step}: {type(exc).__name__}: {exc}"


def parse_step_error(err: str) -> tuple[str, str, str] | None:
    """Parse a canonical step-error string into ``(step, exc_type, message)``.

    Returns ``None`` when the string does not match the canonical shape (i.e. it
    was written by some other producer / a drifted format). The caller then relies
    on the structured :class:`StepError` records carried on state. Never raises.
    """
    # Canonical shape needs at least "<step>: <ExcType>: ...".
    first = err.split(": ", 1)
    if len(first) != 2:
        return None
    step, rest = first[0].strip(), first[1]
    second = rest.split(": ", 1)
    exc_type = second[0].strip()
    message = second[1] if len(second) == 2 else ""
    if not step or not exc_type:
        return None
    return step, exc_type, message
