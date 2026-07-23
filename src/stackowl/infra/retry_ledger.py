"""Turn-scoped carrier for retry/circuit-breaker events (Workstream B).

Lives in ``infra/`` (the base layer) so any layer can record a retry event
WITHOUT a dependency inversion — the THIRD instance of the same idiom already
proven by ``infra/trace.py`` (``TraceContext``) and ``infra/recovery_context.py``
(``record_recovery``/``get_recovery``): a module-level ``ContextVar``, a
``bind()``/``reset()`` pair the backend calls at the same seam in both
``pipeline/backends/asyncio_backend.py`` and ``langgraph_backend.py``, and a
non-consuming ``get_retry()`` reader. Verified against both files before
building this — not a new design.

Write-mostly, read-almost-never: 10 retry/circuit-breaker layers exist across
this codebase (SDK auto-retry, ``resilient_round()``, circuit-breaker
adaptive backoff, circuit-breaker rate-limit cooldown, rate-limiter penalty,
same-tier retry-once, ``LLMGateway``'s tier-escalation ladder, tier cascade,
the app-level goal retry via ``RetryActuator``, and the cron sweep that
drives it) with NO shared observability across them today — a request
retried 6 times across 3 layers leaves no single trace showing that. This
ledger is the write side of closing that gap; the read side is the
``[retry] turn summary`` log line each backend emits once per turn (mirrors
``recovery_context``'s own ``[recovery] turn summary``). No layer gains a new
DECISION-CHANGING read of another layer's retry count — recording here is an
observability side-channel only, never a coupling between retry layers.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from stackowl.infra.observability import log


@dataclass(frozen=True)
class RetryEvent:
    """One retry/circuit-breaker event this turn (recorded by the layer that performed it).

    ``kind`` is the layer-specific event tag (e.g. ``"circuit_open_skip"``,
    ``"cooldown_skip"``, ``"rate_limit_penalty"``, ``"same_tier_retry"``,
    ``"tier_escalation"``, ``"goal_retry_attempt"``, ``"goal_retry_exhausted"``)
    — never a hardcoded enum here, so a new layer can record its own without
    editing this module. ``provider`` is the provider/tier name involved.
    ``attempt_number`` is set only by the app-level goal-retry layer
    (``RetryActuator``); every provider-layer event leaves it ``None``.
    """

    kind: str
    provider: str
    detail: str = ""
    attempt_number: int | None = None


_events: ContextVar[tuple[RetryEvent, ...] | None] = ContextVar(
    "retry_ledger_events", default=None,
)


def bind() -> Token[tuple[RetryEvent, ...] | None]:
    """Install a fresh empty retry-ledger context for one turn. Returns a reset token."""
    return _events.set(())


def reset(token: Token[tuple[RetryEvent, ...] | None]) -> None:
    """Restore the prior retry-ledger context (call in a ``finally``)."""
    _events.reset(token)


def record_retry(
    *, kind: str, provider: str, detail: str = "", attempt_number: int | None = None,
) -> None:
    """Record a retry/circuit-breaker event. No-op (logged) when unbound; never raises."""
    current = _events.get()
    if current is None:
        log.engine.debug(
            "[retry_ledger] record_retry: unbound turn — ignoring",
            extra={"_fields": {"kind": kind, "provider": provider}},
        )
        return
    _events.set((*current, RetryEvent(
        kind=kind, provider=provider, detail=detail, attempt_number=attempt_number,
    )))


def get_retry() -> tuple[RetryEvent, ...]:
    """Non-consuming read of this turn's retry events (empty if none/unbound)."""
    return _events.get() or ()
