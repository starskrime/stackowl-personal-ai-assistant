"""A plan belongs to the turn that wrote it, not to the process.

THE DEFERRAL THIS CLOSES, stated by the store's own docstring:

    "Lifetime note: this store is **process-level** — there is no per-session
    signal yet, so a single agent loop (the common case) is assumed. ... revisit
    when a per-session plan slot lands."

The precondition is now met. ``TraceContext`` carries the turn identity across
async hops, and as of 2026-08-29 even background scheduler jobs bind one.

WHY IT MATTERS NOW AND DID NOT BEFORE — measured, not assumed. ``ToolRegistry
.with_defaults()`` is called ONCE at startup (orchestrator.py:920), so the single
``PlanStore`` it builds is shared by every turn, every chat and every owl for the
life of the process. That has never bitten because the plan tools are effectively
unused: across every retained log, ``todo`` has been invoked **4 times** and
``update_plan`` **once**, against 1,785 web_search and 1,678 web_fetch calls.

So the plan element exists and is decoration. The moment it is actually used —
which is the point of the work this belongs to — a process-global slot means one
chat's plan overwrites another's, and this platform serves concurrent chats by
design ("within-chat serialized, parallel across chats").

Scoping FIRST is therefore not tidiness, it is the precondition for making
planning happen at all. Bounded and MRU-evicting, mirroring TurnCostLedger — the
in-tree shape for exactly this, rather than a second mechanism.
"""

from __future__ import annotations

from stackowl.infra.trace import TraceContext
from stackowl.tools.planning.store import PlanStore


def _items(*ids: str) -> list[dict[str, object]]:
    return [{"id": i, "content": f"step {i}", "status": "pending"} for i in ids]


def test_two_turns_do_not_share_a_plan() -> None:
    """The defect. Chat B must not inherit or clobber chat A's checklist."""
    store = PlanStore()

    a = TraceContext.start(session_key="owl:secretary:telegram:dm:1", trace_id="trace-a")
    try:
        store.replace(_items("1", "2"))
        assert store.counts()["total"] == 2
    finally:
        TraceContext.reset(a)

    b = TraceContext.start(session_key="owl:secretary:slack:dm:2", trace_id="trace-b")
    try:
        assert store.counts()["total"] == 0, (
            "turn B opened onto turn A's plan — one chat is reading another's "
            f"checklist: {store.as_dicts()}"
        )
        store.replace(_items("9"))
        assert store.counts()["total"] == 1
    finally:
        TraceContext.reset(b)

    a2 = TraceContext.start(session_key="owl:secretary:telegram:dm:1", trace_id="trace-a")
    try:
        assert [i.id for i in store.read()] == ["1", "2"], (
            f"turn B destroyed turn A's plan: {store.as_dicts()}"
        )
    finally:
        TraceContext.reset(a2)


def test_status_updates_land_on_the_RIGHT_turn() -> None:
    """merge() is the hot path — a mis-scoped status flip corrupts both plans."""
    store = PlanStore()

    a = TraceContext.start(trace_id="t-a")
    try:
        store.replace(_items("1", "2"))
    finally:
        TraceContext.reset(a)

    b = TraceContext.start(trace_id="t-b")
    try:
        store.replace(_items("1"))
        store.merge([{"id": "1", "status": "completed"}])
        assert store.counts()["completed"] == 1
    finally:
        TraceContext.reset(b)

    a2 = TraceContext.start(trace_id="t-a")
    try:
        assert store.counts()["completed"] == 0, (
            "a completion from another turn leaked into this plan"
        )
        assert store.counts()["pending"] == 2
    finally:
        TraceContext.reset(a2)


def test_the_injected_render_is_per_turn() -> None:
    """format_for_injection feeds the model's own context — the worst place to leak."""
    store = PlanStore()

    a = TraceContext.start(trace_id="t-a")
    try:
        store.replace([{"id": "1", "content": "book the flight", "status": "pending"}])
    finally:
        TraceContext.reset(a)

    b = TraceContext.start(trace_id="t-b")
    try:
        rendered = store.format_for_injection()
        assert rendered is None or "book the flight" not in rendered, (
            f"another turn's plan was injected into this turn's context: {rendered}"
        )
    finally:
        TraceContext.reset(b)


def test_a_caller_with_NO_trace_still_works() -> None:
    """The CLI, tests and any un-traced caller must keep the old behaviour.

    Fail-safe: no trace is a real state (a direct tool invocation), and it must
    get a working plan rather than an exception.
    """
    store = PlanStore()
    store.replace(_items("1"))
    assert store.counts()["total"] == 1
    assert store.has_items()
    store.clear()
    assert not store.has_items()


def test_the_store_is_BOUNDED() -> None:
    """An unbounded per-trace map is a leak that grows for the life of the process."""
    store = PlanStore(max_tracked_turns=8)
    for n in range(40):
        tok = TraceContext.start(trace_id=f"t-{n}")
        try:
            store.replace(_items("1"))
        finally:
            TraceContext.reset(tok)
    assert store.tracked_turns() <= 8, (
        f"the plan store grew to {store.tracked_turns()} turns and never evicts"
    )


def test_the_MOST_RECENT_turns_survive_eviction() -> None:
    """Evicting the turn currently running would silently empty its plan."""
    store = PlanStore(max_tracked_turns=3)
    for n in range(6):
        tok = TraceContext.start(trace_id=f"t-{n}")
        try:
            store.replace(_items("1"))
        finally:
            TraceContext.reset(tok)

    recent = TraceContext.start(trace_id="t-5")
    try:
        assert store.counts()["total"] == 1, "the newest turn's plan was evicted"
    finally:
        TraceContext.reset(recent)
