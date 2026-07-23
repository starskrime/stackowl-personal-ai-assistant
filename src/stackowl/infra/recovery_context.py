"""Turn-scoped carrier for machinery-recorded recovery events.

Lives in ``infra/`` (the base layer) so any layer can record a recovery WITHOUT
a dependency inversion — mirrors ``infra/trace.py``'s ContextVar idiom. The
backend ``bind()``s a fresh context at turn start and ``reset()``s it in a
``finally``; recovery sites (e.g. capability substitution) call
``record_recovery``; the render step and the per-turn log read via the
NON-consuming ``get_recovery``.

**Mutable container, not immutable-tuple-via-set()** (same fix as
``retry_ledger.py``/``decision_ledger.py`` — see ``retry_ledger.py``'s
docstring for the full empirical proof): both ``asyncio.gather`` (confirmed
live in ``execute.py``'s LAT.3 concurrent provider resolution) and
``LangGraphBackend``'s ``compiled.ainvoke(...)`` (confirmed against the real
library) run a coroutine/graph-node in its OWN Task with a COPIED context —
a ``record_recovery`` call made inside never reached the caller's own
context under the OLD immutable-tuple-via-``.set()`` design. The ContextVar
now holds a ``list`` mutated in place; a copied context still references the
SAME list object, so the mutation survives the isolated Task. ``replay()``
below predates this fix (it bridges the SAME class of gap via an explicit
diff-and-reappend) and still works correctly, but is no longer the only way
to close it — kept as-is (not removed) since its existing callers/tests are
unaffected and still pass.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from stackowl.infra.observability import log


@dataclass(frozen=True)
class RecoveryEvent:
    """One machinery recovery this turn (recorded by the code that performed it)."""

    kind: str
    failed: str
    recovered_via: str
    detail: str
    user_visible: bool


_events: ContextVar[list[RecoveryEvent] | None] = ContextVar(
    "recovery_events", default=None,
)


def bind() -> Token[list[RecoveryEvent] | None]:
    """Install a fresh empty recovery context for one turn. Returns a reset token."""
    return _events.set([])


def reset(token: Token[list[RecoveryEvent] | None]) -> None:
    """Restore the prior recovery context (call in a ``finally``)."""
    _events.reset(token)


def record_recovery(
    *, kind: str, failed: str, recovered_via: str,
    detail: str = "", user_visible: bool,
) -> None:
    """Record a recovery event. No-op (logged) when unbound; never raises."""
    current = _events.get()
    if current is None:
        log.engine.debug(
            "[recovery_context] record_recovery: unbound turn — ignoring",
            extra={"_fields": {"kind": kind, "failed": failed}},
        )
        return
    # In-place append (not .set()) — see module docstring: this is what lets
    # the event survive an asyncio.gather/LangGraph-node isolated Task.
    current.append(RecoveryEvent(
        kind=kind, failed=failed, recovered_via=recovered_via,
        detail=detail, user_visible=user_visible,
    ))


def get_recovery() -> tuple[RecoveryEvent, ...]:
    """Non-consuming read of this turn's recovery events (empty if none/unbound)."""
    current = _events.get()
    return tuple(current) if current is not None else ()


def replay(events: tuple[RecoveryEvent, ...]) -> None:
    """Re-append already-built events onto the CURRENT context.

    Predates the mutable-container fix above (see module docstring) — kept
    for ``execute.py``'s existing explicit diff-and-reappend bridge, which
    still works correctly (now belt-and-suspenders rather than load-bearing
    for THAT specific call site, but harmless and unchanged). No-op (logged)
    when unbound.
    """
    if not events:
        return
    current = _events.get()
    if current is None:
        log.engine.debug(
            "[recovery_context] replay: unbound turn — ignoring",
            extra={"_fields": {"n_events": len(events)}},
        )
        return
    current.extend(events)
