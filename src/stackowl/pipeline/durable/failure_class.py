"""Naming what went wrong, once, for the whole durable loop.

WHY THIS MODULE EXISTS AT ALL. The rule "which failures are worth retrying" was
about to have two implementations in one subsystem: ``loop.py`` classified an
EXCEPTION (it catches one from the runner), and the store needed to classify a
STRING (the ``result`` text a caller writes with ``update_status(..., "failed")``).
Same vocabulary, same consequence, two homes — the two-copies-of-one-rule shape
this codebase keeps paying to fix. One source; both callers ask it.

WHY IT IS A LEAF. ``loop.py`` states, in its own ``_Store`` Protocol, that it must
be drivable by a test double "without importing the database". Putting the shared
rule in ``store.py`` would drag the DB import chain into the loop and break that.
This module imports nothing of the platform except the logger.

THE DEFAULT IS RETRY, AND THAT IS THE LOAD-BEARING CHOICE. An unrecognised failure
returns "" — not permanent. A wrong "permanent" strands a task that would have
succeeded, which is the exact give-up the loop exists to prevent; a wrong
"retryable" only spends attempts the ceiling already bounds. Measured 2026-08-18:
850 rows sat in a terminal ``failed`` state having never once been retried.
"""

from __future__ import annotations

from stackowl.infra.observability import log

#: Failure classes meaning "this approach cannot work — change the SHAPE of the
#: work" rather than "try again". Budget exhaustion is the whole set today:
#: re-running something that already spent its ceiling spends it again and dies at
#: the same step. A set, so a second such class costs one line not one branch.
_RESHAPING_CLASSES = frozenset({"budget"})

#: Substring -> class, checked in order, against a casefolded message.
#: SUBSTRINGS rather than regexes on purpose: these match provider prose that
#: changes wording between versions, and a regex that quietly stops matching
#: downgrades a permanent error to retryable without anyone noticing.
#:
#: Order matters. ``budget:stop:`` is the platform's OWN marker (written by
#: ``pipeline/steps/execute.py``) so it is checked first and cannot be shadowed by
#: a generic word occurring in the same string.
_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("budget:stop:", "cost_budget_stopped"), "budget"),
    (("401", "403", "unauthorized", "forbidden", "invalid api key",
      "authentication", "permission denied"), "auth"),
    (("404", "not found", "no such file", "does not exist"), "not_found"),
    (("timeout", "timed out", "deadline exceeded"), "timeout"),
    (("connection refused", "connection reset", "temporarily unavailable",
      "503", "502", "econnrefused"), "transient"),
)


def _from_exception(exc: BaseException) -> str:
    """Classify a raised failure by TYPE, then by its text.

    Reuses the project's single transient oracle rather than adding a second
    opinion about what "transient" means.
    """
    try:
        from stackowl.infra.resilience import looks_like_dead_handle

        if looks_like_dead_handle(exc):
            return "transient"
    except Exception:  # pragma: no cover — the oracle must never decide the turn
        log.tasks.warning("[loop] transient oracle unavailable", exc_info=True)
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "transient"
    if isinstance(exc, PermissionError):
        return "auth"
    if isinstance(exc, FileNotFoundError | LookupError):
        return "not_found"
    # Fall through to the text rules: a plain RuntimeError carrying "401
    # Unauthorized" is an auth failure whatever its Python type says.
    return classify_failure(str(exc))


def classify_failure(error: str | BaseException | None) -> str:
    """Name the failure so the ceiling is spent intelligently. "" when unknown.

    Accepts either the exception the loop caught or the text a caller recorded,
    because both arrive in practice and both mean the same thing to the retry
    policy. "" is the honest answer for "no idea", and it keeps the task retryable.
    """
    if error is None:
        return ""
    if isinstance(error, BaseException):
        return _from_exception(error)
    text = error.strip().casefold()
    if not text:
        return ""
    for needles, cls in _PATTERNS:
        if any(n in text for n in needles):
            return cls
    return ""


def wants_reshaping(failure_class: str) -> bool:
    """Should the next attempt change the shape of the work instead of repeating?

    This is what makes ``should_decompose`` reachable. Without it, a budget
    exhaustion retries identically until the ceiling — burning the same spend each
    time and failing at the same step.
    """
    return failure_class in _RESHAPING_CLASSES
