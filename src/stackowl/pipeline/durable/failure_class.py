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
    # A checkpoint that no longer validates cannot be resumed, and retrying
    # reproduces it exactly. Named so `fail_and_requeue` can drop the
    # checkpoint and let the next attempt start clean.
    (("resume transcript", "resumetranscripterror", "invalid resume"),
     "corrupt_state"),
    (("timeout", "timed out", "deadline exceeded"), "timeout"),
    (("connection refused", "connection reset", "temporarily unavailable",
      "503", "502", "econnrefused"), "transient"),
    # ESC-53. Work created without the fields its handler requires cannot be
    # rescued by repeating it — the nine malformed rollover_summary jobs never
    # succeeded ONCE. Classified into the pre-existing "permanent" class rather
    # than a new name, so the vocabulary does not grow for one case. Checked LAST:
    # a message that also mentions a timeout or a 503 is better described by that.
    (("malformed job",), "permanent"),
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


#: Fallback when no settings are wired. The REAL list is
#: ``settings.task_loop.permanent_failure_classes`` — which failures are truly
#: permanent is deployment-specific (what is unrecoverable behind one gateway is a
#: transient blip behind another), so it must not be a constant compiled in here.
#:
#: MOVED HERE from ``pipeline/durable/store.py`` on 2026-08-24 (ESC-53). The
#: scheduler needed the same answer for one-shot re-arm, and a second copy of
#: "which failures are permanent" is the exact shape this module exists to
#: prevent. The leaf property is preserved: the settings import is lazy, inside
#: the function, so importing this module still pulls in nothing but the logger.
_PERMANENT_CLASSES_FALLBACK = frozenset({"permanent", "auth", "not_found", "refused"})


def permanent_classes() -> frozenset[str]:
    """The configured permanent-failure classes, or the fallback.

    Read at call time, not import time, so a settings change takes effect without
    a redeploy — and never raises: an unreadable config degrades to the fallback
    rather than making every failure look retryable.
    """
    try:
        from stackowl.pipeline.services import get_services

        cfg = getattr(get_services(), "settings", None)
        if cfg is not None:
            return frozenset(cfg.task_loop.permanent_failure_classes)
    except Exception as exc:
        log.tasks.warning(
            "[loop] could not read permanent_failure_classes — using the fallback",
            exc_info=exc,
        )
    return _PERMANENT_CLASSES_FALLBACK


def is_permanent(failure_class: str) -> bool:
    """Can repeating this work ever succeed?

    False for the unknown class (""), which is the load-bearing default stated at
    the top of this module: a wrong "permanent" strands work that would have
    succeeded; a wrong "retryable" only spends attempts that backoff bounds.
    """
    return bool(failure_class) and failure_class in permanent_classes()


def wants_reshaping(failure_class: str) -> bool:
    """Should the next attempt change the shape of the work instead of repeating?

    This is what makes ``should_decompose`` reachable. Without it, a budget
    exhaustion retries identically until the ceiling — burning the same spend each
    time and failing at the same step.
    """
    return failure_class in _RESHAPING_CLASSES


#: Failure classes that get a SMALL retry ceiling instead of the ordinary 30.
#:
#: What they share: the turn already SPOKE to the user — an apology for an effect
#: measured absent, a refusal on a blocked capability, or an honest floor. Each
#: further attempt can produce another message, so a 30-attempt budget is spent on
#: the operator's notifications rather than on solving anything. A few tries and one
#: dead-letter escalation is the honest trade.
#:
#: `floored_turn` joined on 2026-08-29 and it is LOAD-BEARING, not cosmetic: with
#: suppression (deliver holds a floor while the loop still has attempts) the ceiling
#: IS the delivery deadline. On the default 30-attempt budget with backoff
#: (5, 15, 60, 300, 900) the operator would wait hours in silence; at three attempts
#: the worst case is ~80 seconds, after which the dead-letter path delivers the held
#: floor. Changing this number changes how long a user waits with nothing.
#:
#: ONE SOURCE. store.fail_and_requeue used to carry these as a literal tuple; the
#: third entry is what made a second copy worth removing. Exported as the frozenset
#: rather than behind an accessor: unlike `permanent_classes()` beside it, there is
#: no config to read at call time, and a function wrapping a constant for one caller
#: is a layer that earns nothing today.
SMALL_CEILING_CLASSES = frozenset({
    "unachieved_effect", "blocked_capability", "floored_turn",
})
