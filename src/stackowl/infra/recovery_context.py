"""Turn-scoped carrier for machinery-recorded recovery events.

Lives in ``infra/`` (the base layer) so any layer can record a recovery WITHOUT
a dependency inversion — mirrors ``infra/trace.py``'s ContextVar idiom. The
backend ``bind()``s a fresh context at turn start and ``reset()``s it in a
``finally``; recovery sites (e.g. capability substitution) call
``record_recovery``; the render step and the per-turn log read via the
NON-consuming ``get_recovery``.
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


_events: ContextVar[tuple[RecoveryEvent, ...] | None] = ContextVar(
    "recovery_events", default=None,
)


def bind() -> Token[tuple[RecoveryEvent, ...] | None]:
    """Install a fresh empty recovery context for one turn. Returns a reset token."""
    return _events.set(())


def reset(token: Token[tuple[RecoveryEvent, ...] | None]) -> None:
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
    _events.set((*current, RecoveryEvent(
        kind=kind, failed=failed, recovered_via=recovered_via,
        detail=detail, user_visible=user_visible,
    )))


def get_recovery() -> tuple[RecoveryEvent, ...]:
    """Non-consuming read of this turn's recovery events (empty if none/unbound)."""
    return _events.get() or ()


def replay(events: tuple[RecoveryEvent, ...]) -> None:
    """Re-append already-built events onto the CURRENT context.

    Bridges ``asyncio.gather``'s context-isolation boundary: a coroutine
    started via ``gather`` runs inside its own COPIED context (a snapshot
    taken when the Task was created), so a ``record_recovery`` call made
    inside it mutates only that copy — the parent's context is never updated,
    even though the coroutine's return VALUE does reach the caller normally.
    A caller that captured events recorded inside such a child (by diffing
    ``get_recovery()`` before/after the child ran) replays them here, in the
    parent, so they become visible to the parent's own ``get_recovery()``
    readers (e.g. ``surface_recovery``). No-op (logged) when unbound.
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
    _events.set((*current, *events))
