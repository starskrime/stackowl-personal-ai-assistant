"""Turn-scoped carrier for human-wait seconds (time blocked on a human answer).

The per-turn budget clock (``BudgetGovernor`` wall-clock cap) must NOT count time
the turn spends blocked waiting for a human to answer a clarify prompt — otherwise
a slow human pushes a fast turn over its compute ceiling and it "stops with
partial" (a real Telegram incident: ~48s of a 120s budget was human-wait).

Tools and the clarify gateway cannot reach the immutable ``PipelineState``; they
only see ambient context. This module mirrors the ``lesson_context`` /
``recovery_context`` ContextVar idiom to accumulate, for the duration of ONE turn,
the seconds spent inside ``ClarifyGateway.wait_for_answer``. The backend
``bind()``s a fresh zero accumulator at turn start and ``reset()``s it in a
``finally`` — so nothing leaks across turns or across concurrent turns (each turn
runs in its own async task; ContextVars are per-context).

Byte-identical baseline: when no clarify wait occurs the accumulator stays 0.0 and
the governor's effective elapsed is unchanged. A governor constructed without a
``human_wait_source`` (most unit tests / non-turn callers) is also unaffected.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_human_wait_s: ContextVar[float] = ContextVar("human_wait_seconds", default=0.0)


def bind() -> Token[float]:
    """Install a fresh zero human-wait accumulator for one turn. Returns a reset token."""
    return _human_wait_s.set(0.0)


def reset(token: Token[float]) -> None:
    """Restore the prior human-wait context (call in a ``finally``)."""
    _human_wait_s.reset(token)


def record_human_wait(seconds: float) -> None:
    """Add blocked-on-human seconds to the current turn's accumulator.

    Non-positive or NaN values are ignored. Never raises — budget accounting must
    never crash a turn. Outside a bound context this accumulates onto the 0.0
    default for the current context, which is harmless.
    """
    if not seconds > 0.0:  # also rejects NaN (NaN > 0.0 is False)
        return
    _human_wait_s.set(_human_wait_s.get() + seconds)


def current_human_wait_seconds() -> float:
    """Accumulated human-wait seconds for the current turn (0.0 if unbound)."""
    return _human_wait_s.get()
