"""The skill catalogue must not move under a live session when a skill runs.

MEASURED 2026-08-30, the last unexplained slice of ESC-67's Law 1 violations: of
259 `[cache] breakpoints: prompt part CHANGED — the cached prefix is lost from
here` warnings, **22 name the `skills` part**, and 15 of those fall inside a single
50-minute window on 2026-08-26 (08:39-09:28) across many short-lived recovery and
objective sessions — exactly when skills were executing heavily.

THE CAUSE IS A MUTABLE SORT INPUT. `catalogue_order_key` is::

    (-n_executions, lifecycle_rank, name)

and ``n_executions`` increments every time a skill runs. So running a skill
reorders the catalogue, the rendered block changes bytes, and every live session's
cached prefix is lost.

THE EXISTING ARGUMENT HAS A PRECISE GAP, and it is worth naming because the code
is otherwise careful. Both `catalogue_order_key` and its caller argue Law 1 is
safe:

    "This key is equally deterministic and, because `name` is its final term,
    TOTAL: two distinct skills can never compare equal, so the rendered block can
    never depend on the order SQLite happened to return rows in."
    "...so byte-identity and Law 1 are untouched."

That proves the block does not depend on ROW ORDER. It does not prove the block is
stable over TIME, because one of its inputs changes. DETERMINISTIC IS NOT STABLE —
a pure function of mutable state still moves when the state does.

THE FIX FOLLOWS THE PROFILE PRECEDENT, set in the same file's neighbouring block:
"read from a snapshot frozen for the life of this incarnation, so a write made
mid-session lands on disk immediately but does not move the prompt until the next
/new." The catalogue gets the same treatment and the same accepted tradeoff — a
newly authored skill appears on the next /new rather than mid-session. That matters
more now, not less: the incident miner authors skills continuously, and without the
freeze each one would invalidate every live session.

ESC-44's decision is untouched: ordering is still by measured value, merely sampled
once per incarnation instead of once per turn.
"""

from __future__ import annotations

from stackowl.skills.catalogue_snapshot import CatalogueSnapshot


def test_a_skill_running_does_not_move_a_live_sessions_catalogue() -> None:
    """The defect: n_executions changes, the sort flips, the prefix is lost."""
    snap = CatalogueSnapshot()
    calls = {"n": 0}

    def _render() -> str:
        calls["n"] += 1
        return f"catalogue-v{calls['n']}"

    first = snap.get("conv-A", _render)
    # a skill executes here — n_executions changes and _render would reorder
    again = snap.get("conv-A", _render)

    assert first == again, (
        f"the catalogue moved mid-session: {first!r} -> {again!r}. Every live "
        "session's cached prefix is lost when any skill runs."
    )
    assert calls["n"] == 1, "the catalogue was re-rendered within one conversation"


def test_a_NEW_incarnation_picks_up_a_newly_authored_skill() -> None:
    """The freeze must not become a cache that never refreshes.

    The incident miner authors skills continuously; they must reach the prompt on
    the next /new, exactly as a curated-memory write does.
    """
    snap = CatalogueSnapshot()
    calls = {"n": 0}

    def _render() -> str:
        calls["n"] += 1
        return f"catalogue-v{calls['n']}"

    snap.get("conv-A", _render)
    fresh = snap.get("conv-A-2", _render)
    assert fresh == "catalogue-v2", "a new incarnation did not re-read the catalogue"


def test_two_conversations_do_not_evict_each_other() -> None:
    """The single-slot bug fixed twice already today must not be rebuilt here."""
    snap = CatalogueSnapshot()
    calls = {"n": 0}

    def _render() -> str:
        calls["n"] += 1
        return f"catalogue-v{calls['n']}"

    a1 = snap.get("conv-A", _render)
    snap.get("conv-B", _render)
    a2 = snap.get("conv-A", _render)
    assert a1 == a2, f"conversation B evicted A's frozen catalogue: {a1!r} -> {a2!r}"


def test_it_is_BOUNDED() -> None:
    """An unbounded per-conversation map leaks for the life of the process."""
    snap = CatalogueSnapshot(max_tracked=16)
    for i in range(200):
        snap.get(f"conv-{i}", lambda: "x")
    assert snap.tracked() <= 16


def test_the_RECENTLY_USED_conversation_survives_eviction() -> None:
    """Evicting the live conversation would restore the very bug this removes.

    Corrected after the first version of this test asserted the wrong thing: it
    inserted `conv-newest` FIRST and then three others, which makes it the LEAST
    recently used, so evicting it is correct. The invariant that matters is that a
    conversation still being USED survives — so it is touched again before the
    pressure arrives, exactly as a live session would be.
    """
    snap = CatalogueSnapshot(max_tracked=3)
    calls = {"n": 0}

    def _render() -> str:
        calls["n"] += 1
        return f"v{calls['n']}"

    live = snap.get("conv-live", _render)
    for i in range(2):
        snap.get(f"conv-other-{i}", _render)
        snap.get("conv-live", _render)  # the live session keeps taking turns
    snap.get("conv-late", _render)

    assert snap.get("conv-live", _render) == live, (
        "the conversation currently taking turns was evicted, so its catalogue "
        "re-renders and its prefix is lost — the bug this replaced"
    )


def test_a_render_failure_is_NOT_cached() -> None:
    """A transient failure must not freeze an empty catalogue for the session.

    Caching "" would silently strip the catalogue from every remaining turn of
    that conversation — a much worse outcome than one extra render.
    """
    snap = CatalogueSnapshot()
    assert snap.get("conv-A", lambda: "") == ""
    assert snap.get("conv-A", lambda: "real-catalogue") == "real-catalogue"
