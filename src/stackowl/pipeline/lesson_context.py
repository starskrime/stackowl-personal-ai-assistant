"""Turn-scoped carrier for surfaced lessons + the model's self-reported uses.

Tools cannot reach the immutable ``PipelineState``; they only see ambient
context. This module mirrors the ``TraceContext`` ContextVar idiom to carry, for
the duration of ONE turn:
  * the lessons the classify step surfaced (so a tool can resolve an ``L#`` id), and
  * the lessons the model reported it acted on (so the delivery step can explain).

The backend ``bind()``s a fresh, empty context at turn start and ``reset()``s it
in a ``finally`` — so nothing leaks across turns or across concurrent turns
(each turn runs in its own async task; ContextVars are per-context).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import NamedTuple

from stackowl.infra import decision_ledger
from stackowl.infra.observability import log


@dataclass(frozen=True)
class SurfacedLesson:
    """A lesson injected into the prompt this turn, addressable by a turn-local id."""

    lesson_id: str
    source_type: str
    content: str
    similarity: float


@dataclass(frozen=True)
class AppliedLesson:
    """The model's self-report that it acted on a surfaced lesson."""

    lesson_id: str
    what_you_did: str
    lesson_summary: str | None


_surfaced: ContextVar[tuple[SurfacedLesson, ...]] = ContextVar(
    "surfaced_lessons", default=(),
)
_applied: ContextVar[tuple[AppliedLesson, ...] | None] = ContextVar(
    "applied_lessons", default=None,
)


class _LessonToken(NamedTuple):
    surfaced: Token[tuple[SurfacedLesson, ...]]
    applied: Token[tuple[AppliedLesson, ...] | None]


def bind() -> _LessonToken:
    """Install a fresh empty lesson context for one turn. Returns a reset token."""
    return _LessonToken(surfaced=_surfaced.set(()), applied=_applied.set(()))


def reset(token: _LessonToken) -> None:
    """Restore the prior lesson context (call in a ``finally``)."""
    _surfaced.reset(token.surfaced)
    _applied.reset(token.applied)


def set_surfaced(lessons: tuple[SurfacedLesson, ...]) -> None:
    """Record the lessons surfaced this turn (called by the classify step)."""
    _surfaced.set(lessons)


def get_surfaced() -> tuple[SurfacedLesson, ...]:
    return _surfaced.get()


def record_applied(lesson_id: str, what_you_did: str) -> SurfacedLesson | None:
    """Record that the model acted on ``lesson_id``. Returns the matched surfaced
    lesson (or None if the id was not surfaced this turn). No-op if unbound."""
    current = _applied.get()
    if current is None:
        log.engine.debug(
            "[lesson_context] record_applied: unbound turn — ignoring",
            extra={"_fields": {"lesson_id": lesson_id}},
        )
        return None
    match = next((s for s in _surfaced.get() if s.lesson_id == lesson_id), None)
    if match is None:
        log.engine.debug(
            "[lesson_context] record_applied: unknown lesson id",
            extra={"_fields": {"lesson_id": lesson_id}},
        )
    _applied.set((*current, AppliedLesson(
        lesson_id=lesson_id,
        what_you_did=what_you_did,
        lesson_summary=match.content if match is not None else None,
    )))
    # ADR-7: this is the point a learned heuristic actually STEERED the turn — emit one
    # ``learned_context`` Decision so "which lesson steered you, and how?" is a read of
    # the turn ledger. Independently no-ops when the ledger is unbound.
    decision_ledger.record_decision(
        point="learned_context",
        verdict=lesson_id,
        reason=what_you_did,
        evidence={"matched_surfaced": match is not None},
    )
    return match


def drain_applied() -> tuple[AppliedLesson, ...]:
    """Return the applied-lesson reports for this turn (empty if none/unbound)."""
    return _applied.get() or ()
