"""The live conversation half of MemoryBridge is a type of its own.

D08.2 slice A. `MemoryBridge` has served two callers since Epic 6, and its own
docstring says so — a pipeline half (`retrieve`, `store`, and later
`recent_conversation_turns` / `clear_session`) and a knowledge-pipeline half
(`stage`, `recall`, `delete`, `list_staged`, `find_committed_by_prefix`). The
seam was documented and never drawn.

MEASURED 2026-08-14, correcting the "31 files, so this cannot be done casually"
figure this item started with. Of every file outside `src/stackowl/memory/` that
names the bridge:

    live-half only        3   turn_persist, classify, commands/reset
    dead-half only       11   the /memory surface, the three channel callbacks,
                              the brief, browser recall, parliament, orchestrator
    USES BOTH             0
    type/mention only    16   annotations, no method calls

**Nothing uses both halves.** They were already disjoint at file level, so the
split is far cheaper than budgeted — 3 files to move, not 31. The 31 was a count
of files that MENTION the bridge, which is not the same question.

It still matters that the halves stopped being equally alive: the fact half
reads `committed_facts`, 0 rows since D08.1's migration 0112 and no writers
left, while the conversation half is load-bearing on every turn. A consumer that
needs only the live half should not be able to reach the dead one.

`ConversationStore` is that narrow type, and it is ADDITIVE — nothing removed,
no behaviour changed.
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


def test_reset_works_with_only_the_live_half() -> None:
    """`/reset` wipes the session's conversation buffer and nothing else, so it
    must run against a bare ConversationStore — no fact-half methods present.

    This is the seam actually paying for itself: the object below implements
    five methods and would not satisfy MemoryBridge at all.
    """
    import asyncio

    from stackowl.commands.reset import ResetCommand
    from stackowl.pipeline.state import PipelineState

    cleared: list[str] = []

    class _LiveHalfOnly:
        async def retrieve(self, query: str, session_key: str) -> str:
            return ""

        async def store(self, content: str, session_key: str, **kw: object) -> None:
            return None

        async def recent_conversation_turns(self, session_key: str, **kw: object) -> list:
            return []

        async def clear_session(self, session_key: str) -> int:
            cleared.append(session_key)
            return 7

        async def health(self):  # noqa: ANN202
            return None

    cmd = ResetCommand(bridge=_LiveHalfOnly())  # type: ignore[arg-type]
    state = PipelineState(
        trace_id="t", session_key="sess-reset", input_text="/reset",
        channel="cli", owl_name="secretary", pipeline_step="command",
    )
    asyncio.run(cmd.handle("", state))  # type: ignore[arg-type]

    assert cleared == ["sess-reset"], (
        "reset must have cleared the session through the narrow store"
    )


def test_it_is_runtime_checkable_so_wiring_can_assert_on_it() -> None:
    """A protocol that cannot be isinstance-checked cannot be enforced at the
    seam it exists to defend."""
    from stackowl.memory.bridge import ConversationStore, NullMemoryBridge

    assert isinstance(NullMemoryBridge(), ConversationStore)
    assert not isinstance(object(), ConversationStore)
