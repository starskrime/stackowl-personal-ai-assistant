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

**Mutable container, not immutable-tuple-via-set()** (deliberate, verified):
the ContextVar holds a ``list`` that ``record_retry`` appends to IN PLACE,
rather than a tuple replaced via ``.set()`` on every write. This matters
because ``LangGraphBackend`` drives the pipeline via
``compiled.ainvoke(...)`` — confirmed empirically (a minimal real-LangGraph
repro) that each graph node runs in its OWN Task with a COPIED context,
exactly like ``asyncio.gather``/``asyncio.run()``'s well-known isolation:
mutations via ``ContextVar.set()`` made inside a node never reach the
backend's own post-graph ``finally`` block. A COPIED context still holds a
reference to the SAME list object, though — copying a Context copies the
ContextVar→value mapping, not a deep copy of the value — so an in-place
``list.append()`` inside a node IS visible to every other holder of that
reference once the node returns, with no bridging code needed. ``bind()``
still installs a genuinely NEW list per turn (so nested binds still isolate
correctly, matching ``recovery_context``'s proven nested-isolation
semantics) — only the in-scope mutation path changed.
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


_events: ContextVar[list[RetryEvent] | None] = ContextVar(
    "retry_ledger_events", default=None,
)


def bind() -> Token[list[RetryEvent] | None]:
    """Install a fresh empty retry-ledger context for one turn. Returns a reset token."""
    return _events.set([])


def reset(token: Token[list[RetryEvent] | None]) -> None:
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
    # In-place append (not .set()) — see module docstring: this is what lets
    # the event survive a LangGraph node's isolated Task context.
    current.append(RetryEvent(
        kind=kind, provider=provider, detail=detail, attempt_number=attempt_number,
    ))


def get_retry() -> tuple[RetryEvent, ...]:
    """Non-consuming read of this turn's retry events (empty if none/unbound)."""
    current = _events.get()
    return tuple(current) if current is not None else ()
