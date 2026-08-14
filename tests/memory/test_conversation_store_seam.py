"""The live conversation half of MemoryBridge is a type of its own.

D08.2 slice A. `MemoryBridge` has served two callers since Epic 6, and its own
docstring says so — a pipeline half (`retrieve`, `store`, and later
`recent_conversation_turns` / `clear_session`) and a knowledge-pipeline half
(`stage`, `recall`, `delete`, `list_staged`, `find_committed_by_prefix`). The
seam was documented and never drawn, so **31 files outside `src/stackowl/memory/`
import the whole thing** to use a fifth of it.

That mattered once the two halves stopped being equally alive. The fact half
reads `committed_facts`, which has held 0 rows since D08.1's migration 0112 and
has no writers left; the conversation half is load-bearing in `turn_persist`
and `classify` on every single turn. A consumer that only needs the live half
should not be typed against — or able to reach — the dead one.

`ConversationStore` is that narrow type. This is deliberately ADDITIVE: nothing
is removed and no behaviour changes, so the 31 importers can be moved onto it
one seam at a time. D08.1 measured that approach at 5 files touched per stage,
against 140 errors across 45 files for a single sweeping change.
"""

from __future__ import annotations


def test_the_live_half_is_exactly_these_five_methods() -> None:
    """The membership list IS the design decision, so it is asserted.

    If a method is added here it must be because it is load-bearing on a normal
    turn — not because a caller found it convenient to reach through.
    """
    from stackowl.memory.bridge import ConversationStore

    expected = {
        "retrieve",
        "store",
        "recent_conversation_turns",
        "clear_session",
        "health",
    }
    declared = {
        name for name in dir(ConversationStore)
        if not name.startswith("_")
    }
    assert declared == expected, (
        f"ConversationStore's surface drifted: {declared ^ expected}"
    )


def test_the_dead_fact_half_is_absent_from_it() -> None:
    """The point of the split: a live-half consumer cannot reach the fact store.

    Each of these reads or writes the retired extraction pipeline's tables.
    """
    from stackowl.memory.bridge import ConversationStore

    for dead in (
        "stage", "recall", "delete", "list_staged", "find_committed_by_prefix",
    ):
        assert not hasattr(ConversationStore, dead), (
            f"{dead!r} is the fact half — it must not be reachable through "
            "ConversationStore, or the seam buys nothing"
        )


def test_the_real_bridges_satisfy_it() -> None:
    """Structural, not nominal — so no existing bridge has to change its bases.

    Checked against BOTH implementations rather than a stand-in: a seam that
    only the test double satisfies is the failure this codebase keeps finding.
    """
    from stackowl.memory.bridge import ConversationStore, NullMemoryBridge
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    for impl in (NullMemoryBridge, SqliteMemoryBridge):
        for name in ("retrieve", "store", "recent_conversation_turns",
                     "clear_session", "health"):
            assert callable(getattr(impl, name, None)), (
                f"{impl.__name__} is missing {name!r}, so it cannot serve as a "
                "ConversationStore"
            )
        assert isinstance(NullMemoryBridge(), ConversationStore)


def test_it_is_runtime_checkable_so_wiring_can_assert_on_it() -> None:
    """A protocol that cannot be isinstance-checked cannot be enforced at the
    seam it exists to defend."""
    from stackowl.memory.bridge import ConversationStore, NullMemoryBridge

    assert isinstance(NullMemoryBridge(), ConversationStore)
    assert not isinstance(object(), ConversationStore)
