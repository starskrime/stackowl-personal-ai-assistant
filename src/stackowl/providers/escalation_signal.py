"""Turn-scoped bridge between the circuit breaker (pipeline) and the model-tier
escalation ladder (providers).

Layering: providers cannot import ``pipeline`` (the dependency arrow points the
other way), so this one-bit signal lives provider-side. ``_dispatch`` (pipeline)
imports it to SET the flag when a same-tool breaker opens; the provider ReAct
loops import it to READ the flag and return ``ESCALATE_SENTINEL`` so the gateway
re-runs the turn on a stronger tier. Mirrors the ContextVar idiom in
``pipeline/lesson_context.py`` — per-async-context, so nothing leaks across turns
or concurrent turns. The gateway's ``on_escalate`` clears it for the fresh tier.
"""

from __future__ import annotations

from contextvars import ContextVar

from stackowl.infra.observability import log

_requested: ContextVar[bool] = ContextVar("escalation_requested", default=False)


def request_escalation(reason: str = "") -> None:
    """Ask the provider loop to escalate to a stronger tier (breaker dead-end → ladder)."""
    log.engine.debug(
        "[escalation_signal] request_escalation: entry",
        extra={"_fields": {"reason": reason}},
    )
    _requested.set(True)


def escalation_requested() -> bool:
    """True if an escalation was requested this turn (default False if unset)."""
    return _requested.get()


def clear_escalation() -> None:
    """Re-arm for a fresh tier; called on tier escalation."""
    _requested.set(False)
