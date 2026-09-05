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

#: Whether this turn has already said out loud that it cannot escalate. A tool loop
#: can ask many times in one turn and the answer never changes, so the reason is
#: stated ONCE — repeating it per step turns one fact into a burst, which is how a
#: true line becomes noise.
_decline_noted: ContextVar[bool] = ContextVar("escalation_decline_noted", default=False)


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
    """Re-arm for a fresh tier; called on tier escalation.

    Re-arms the decline notice too, so ONE call re-arms the whole signal. The
    ContextVars already default per async context, so nothing leaks between turns
    regardless — this keeps the two flags from needing separate remembering, which
    is how the second one ends up forgotten.
    """
    _requested.set(False)
    _decline_noted.set(False)


def reset_decline_notice() -> None:
    """Re-arm the once-per-turn decline notice (turn boundaries and tests)."""
    _decline_noted.set(False)


def escalation_allowed(*, can_escalate: bool) -> bool:
    """Should this turn escalate — and if not, say why.

    THE ONE PLACE THE DECISION IS MADE. Both provider ReAct loops previously
    open-coded ``if can_escalate and escalation_requested():``, which is two copies
    of one rule — the shape that made the vendor sniff wrong in D04.2 one item
    earlier, and the shape ``CLAUDE.md`` lists among the four that account for
    nearly every real defect here.

    AND IT SAYS WHY IT REFUSED. The pipeline logs "circuit open — requested tier
    escalation" at INFO when a tool's breaker opens; measured 2026-09-05, twelve such
    requests exist in the kept logs and **not one line records the outcome**. Someone
    asking why a turn did not escalate found a request and silence. The honest
    answer — every rung of the ladder resolves to the same model, so there is nothing
    stronger to escalate TO — is exactly the fact D04.4 had to make visible for the
    ladder itself.

    Silent when nothing asked: a line on every turn that never wanted to escalate
    would drown the one that did.
    """
    if not escalation_requested():
        return False
    if can_escalate:
        return True
    if not _decline_noted.get():
        _decline_noted.set(True)
        log.engine.info(
            "[escalation_signal] escalation declined — no stronger tier to reach; "
            "every rung of the ladder resolves to the same model, so the turn "
            "delivers what it has instead of re-running identically",
        )
    return False
