"""Tests for CommandSequenceStore — per-owner command-sequence learning.

Records the dispatched command per turn as a Markov edge (prev → next) and
suggests the most-frequent next commands for the owner's current position. This
is the durable backend behind the TUI ``☆ suggested`` lane ("after A you usually
do B"). Suggest-only: it NEVER fires a command.
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import Arg, CommandMeta, SubCommand
from stackowl.commands.sequence_store import (
    CommandSequenceStore,
    SequenceSuggestion,
    record_dispatch,
)
from stackowl.db.pool import DbPool

pytestmark = pytest.mark.asyncio


async def test_no_history_returns_no_suggestions(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    assert await store.suggest_next("local") == []


async def test_learns_a_then_b(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    # Walk A → B → back to A. Now the owner is "at" A again, and the learned
    # edge A→B should surface.
    await store.record("local", "/parliament")
    await store.record("local", "/memory remember")
    await store.record("local", "/parliament")
    out = await store.suggest_next("local")
    assert out == [SequenceSuggestion(invocation="/memory remember", count=1)]


async def test_frequency_ordering(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    for cmd in ["/a", "/b", "/a", "/c", "/a", "/b", "/a"]:
        await store.record("local", cmd)
    # last == /a → A→B seen twice, A→C once.
    out = await store.suggest_next("local", limit=5)
    assert out == [
        SequenceSuggestion(invocation="/b", count=2),
        SequenceSuggestion(invocation="/c", count=1),
    ]


async def test_self_loop_is_not_recorded(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    await store.record("local", "/memory")
    await store.record("local", "/memory")
    # No A→A edge; nothing to suggest.
    assert await store.suggest_next("local") == []


async def test_min_count_threshold_filters(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    await store.record("local", "/a")
    await store.record("local", "/b")
    await store.record("local", "/a")
    # A→B has count 1; a threshold of 2 hides it.
    assert await store.suggest_next("local", min_count=2) == []
    assert await store.suggest_next("local", min_count=1) == [
        SequenceSuggestion(invocation="/b", count=1)
    ]


async def test_owner_isolation(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    for cmd in ["/a", "/b", "/a"]:
        await store.record("telegram:1", cmd)
    for cmd in ["/a", "/c", "/a"]:
        await store.record("telegram:2", cmd)
    assert await store.suggest_next("telegram:1") == [
        SequenceSuggestion(invocation="/b", count=1)
    ]
    assert await store.suggest_next("telegram:2") == [
        SequenceSuggestion(invocation="/c", count=1)
    ]
    # A third owner has nothing.
    assert await store.suggest_next("telegram:3") == []


async def test_limit_caps_results(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    seq = ["/a", "/b", "/a", "/c", "/a", "/d", "/a"]
    for cmd in seq:
        await store.record("local", cmd)
    out = await store.suggest_next("local", limit=2)
    assert len(out) == 2


# --- record_dispatch: the orchestrator seam --------------------------------


def _memory_meta() -> CommandMeta:
    return CommandMeta(
        grammar="verb",
        subcommands=(
            SubCommand(name="remember", summary="store a fact", args=(Arg(name="text"),)),
        ),
    )


async def test_record_dispatch_records_canonical_path(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    inv = await record_dispatch(store, "memory", _memory_meta(), "remember buy milk", "local")
    assert inv == "/memory remember"


async def test_record_dispatch_skips_dry_run(tmp_db: DbPool) -> None:
    store = CommandSequenceStore(db=tmp_db)
    # A trailing `??` is a preview — not a dispatch, so it is not learned.
    inv = await record_dispatch(store, "memory", _memory_meta(), "remember buy milk ??", "local")
    assert inv is None
    # And nothing was advanced: a following real command forms no edge from it.
    await record_dispatch(store, "memory", _memory_meta(), "remember x", "local")
    assert await store.suggest_next("local") == []
