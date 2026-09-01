"""A plan that dies at the end of the turn cannot do what a plan is for.

MEASURED 2026-09-01 through the E5-S8/S9 smoke test, which drives the real
Telegram → gateway → backend → registry path. Turn 1 writes a two-item plan and
BOTH items render::

    - [>] 1. gather requirements (in_progress)
    - [ ] 2. implement (pending)

Turn 2's ``todo set_status id=2`` answers ``No plan item with id '2'``. The store
was not at fault and neither tool was: instrumenting both showed the SAME
``PlanStore`` object, ``update_plan`` writing ``['1','2']`` under key
``81c9bc461a1e`` and ``todo`` reading under ``f74aaab9d90b`` — two turns, two
trace ids, the plan still sitting untouched in the first bucket.

``PlanStore._turn_key()`` keyed on ``trace_id``, which is minted fresh per TURN.

THE MODULE DOCSTRING ALREADY WANTED THE OTHER THING. It records closing the
deferral "there is no per-session signal yet ... revisit when a per-session plan
slot lands" with the words "the signal exists now" — and then keyed on the
per-turn identifier anyway. Its stated goal is that "one chat's plan overwrites
another's" must not happen, and ``trace_id`` achieves that only by making the
plan so short-lived that nothing can collide with it.

``session_key`` IS the per-session signal: the chat lane, stable across a
conversation's turns and distinct between chats. It meets the stated goal AND
survives the turn. ``conversation_id`` is kept only as a fallback because it is
None on the Telegram path (measured), so it cannot carry this alone.

BOTH PROPERTIES ARE ASSERTED HERE because the fix trades one against the other if
it is wrong in either direction: a key too narrow loses the plan (the bug), a key
too wide leaks one chat's plan into another (the bug the old key was guarding
against). Neither test is meaningful without its sibling.
"""

from __future__ import annotations

from stackowl.infra.trace import TraceContext
from stackowl.tools.planning.store import PlanStore

_CHAT_A = "owl:secretary:telegram:dm:111"
_CHAT_B = "owl:secretary:telegram:dm:222"


def _write(store: PlanStore, session_key: str, content: str, *, trace: str) -> None:
    token = TraceContext.start(session_key=session_key, channel="telegram")
    try:
        store.replace([{"id": "1", "content": content, "status": "in_progress"}])
    finally:
        TraceContext.reset(token)


def _read(store: PlanStore, session_key: str, *, trace: str) -> list[str]:
    token = TraceContext.start(session_key=session_key, channel="telegram")
    try:
        return [item.content for item in store.read()]
    finally:
        TraceContext.reset(token)


def test_a_plan_survives_into_the_next_turn() -> None:
    """The defect: a fresh trace_id per turn made the plan unreachable one turn
    after it was written, which is every turn that would use it."""
    store = PlanStore()
    _write(store, _CHAT_A, "gather requirements", trace="turn-1")
    assert _read(store, _CHAT_A, trace="turn-2") == ["gather requirements"], (
        "the plan did not survive the turn that wrote it — a plan keyed on a "
        "per-turn identifier can never be advanced, which is what plans are for"
    )


def test_another_chat_cannot_see_it() -> None:
    """The expensive direction, and the property the old key was protecting. A
    key wide enough to survive the turn must NOT be wide enough to leak across
    chats — the module docstring names this as the reason per-process storage
    was rejected."""
    store = PlanStore()
    _write(store, _CHAT_A, "chat A plan", trace="turn-1")
    assert _read(store, _CHAT_B, trace="turn-1") == [], (
        "one chat's plan is visible to another — this is the collision the "
        "per-turn key existed to prevent, and it must not return"
    )


def test_the_first_chat_still_has_its_plan_afterwards() -> None:
    """Isolation must not be achieved by clobbering: B looking must not empty A."""
    store = PlanStore()
    _write(store, _CHAT_A, "chat A plan", trace="turn-1")
    _read(store, _CHAT_B, trace="turn-1")
    assert _read(store, _CHAT_A, trace="turn-3") == ["chat A plan"]


def test_a_caller_with_no_lane_still_works() -> None:
    """CLI, a direct tool call, a test: no session_key at all. The store is
    advisory and must never raise, and the shared bucket keeps the pre-existing
    behaviour for those callers."""
    store = PlanStore()
    store.replace([{"id": "1", "content": "untraced", "status": "pending"}])
    assert [i.content for i in store.read()] == ["untraced"]


def test_the_key_prefers_the_lane_over_the_trace() -> None:
    """Structural: falling back to trace_id first would silently restore the
    per-turn behaviour while looking fixed."""
    import inspect

    source = inspect.getsource(PlanStore._turn_key)
    assert source.index('"session_key"') < source.index('"trace_id"'), (
        "trace_id is consulted before session_key — the plan is per-turn again"
    )
