"""One conversation's frozen profile must survive another conversation running.

MEASURED 2026-08-30 while chasing the Law 1 violations behind ESC-67: of 259
`[cache] breakpoints: prompt part CHANGED — the cached prefix is lost from here`
warnings, **89 name the `profile` part** — jobmarket 66, secretary 30, and others.

`profile` is the curated-memory block (USER.md plus the owl's own notes), and
assemble.py states the invariant it is supposed to have::

    "Both are read from a snapshot frozen for the life of this incarnation, so a
    write made mid-session lands on disk immediately but does not move the prompt
    until the next /new."

``snapshot_for_prompt`` implements that with a SINGLE SLOT on a process-wide
singleton::

    if self._snapshot_key != conversation_id:
        self._snapshot = {}
        self._snapshot_key = conversation_id

So the freeze holds only while ONE conversation is running. This platform serves
chats concurrently by design — "within-chat serialized, parallel across chats" —
so conversation B's turn evicts conversation A's snapshot, and A's next turn
re-reads the file. If anything wrote to it in between (the agent writes its own
notes), A's prompt MOVES underneath it and the prefix is lost.

The docstring for ``shared_memory`` names the exact failure it is trying to
prevent — "anything building a system prompt must come through here or it will
re-read the file mid-session and move the prompt underneath itself" — and a
single slot reintroduces it for every conversation but the most recent.

SAME SHAPE AS THE PLAN STORE, fixed earlier today: process-global single-slot
state where per-conversation state was needed, harmless until something ran
concurrently. Bounded and MRU-evicting for the same reason — an unbounded
per-conversation map is a leak that grows for the life of the process.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import CuratedMemory


@pytest.fixture()
def store(tmp_path, monkeypatch):  # noqa: ANN001,ANN201
    """A CuratedMemory rooted in a temp dir, so the real files are untouched."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    return CuratedMemory()


def _write(store: CuratedMemory, target: str, text: str) -> None:
    """Write through whatever public path this store offers."""
    store.remember(target, text)


def test_a_second_conversation_does_not_evict_the_first(store, monkeypatch) -> None:  # noqa: ANN001
    """The defect: A's frozen snapshot is destroyed by B merely taking a turn."""
    rendered = {"n": 0}

    def _fake_render(entries):  # noqa: ANN001,ANN202
        rendered["n"] += 1
        return f"render-{rendered['n']}"

    monkeypatch.setattr(store, "_render", _fake_render)
    monkeypatch.setattr(store, "entries", lambda target: [])

    a1 = store.snapshot_for_prompt("USER.md", conversation_id="conv-A")
    store.snapshot_for_prompt("USER.md", conversation_id="conv-B")
    a2 = store.snapshot_for_prompt("USER.md", conversation_id="conv-A")

    assert a1 == a2, (
        "conversation A's frozen profile changed because conversation B took a "
        f"turn: {a1!r} -> {a2!r}. That moves A's system prompt mid-session and "
        "forfeits its prefix cache — 89 measured invalidations name this part."
    )


def test_the_freeze_still_holds_within_one_conversation(store, monkeypatch) -> None:  # noqa: ANN001
    """The property the single slot DID provide must not be lost in the fix."""
    calls = {"n": 0}

    def _fake_render(entries):  # noqa: ANN001,ANN202
        calls["n"] += 1
        return f"render-{calls['n']}"

    monkeypatch.setattr(store, "_render", _fake_render)
    monkeypatch.setattr(store, "entries", lambda target: [])

    first = store.snapshot_for_prompt("USER.md", conversation_id="conv-A")
    again = store.snapshot_for_prompt("USER.md", conversation_id="conv-A")
    assert first == again
    assert calls["n"] == 1, "the snapshot was re-rendered within one conversation"


def test_a_NEW_incarnation_still_re_reads(store, monkeypatch) -> None:  # noqa: ANN001
    """A write must reach the prompt on the next /new — the point of the freeze.

    A per-conversation cache must not become a cache that never refreshes.
    """
    calls = {"n": 0}

    def _fake_render(entries):  # noqa: ANN001,ANN202
        calls["n"] += 1
        return f"render-{calls['n']}"

    monkeypatch.setattr(store, "_render", _fake_render)
    monkeypatch.setattr(store, "entries", lambda target: [])

    store.snapshot_for_prompt("USER.md", conversation_id="conv-A")
    fresh = store.snapshot_for_prompt("USER.md", conversation_id="conv-A-2")
    assert fresh == "render-2", "a new incarnation did not re-read the file"


def test_it_is_BOUNDED(store, monkeypatch) -> None:  # noqa: ANN001
    """An unbounded per-conversation map leaks for the life of the process."""
    monkeypatch.setattr(store, "_render", lambda entries: "x")
    monkeypatch.setattr(store, "entries", lambda target: [])

    for i in range(400):
        store.snapshot_for_prompt("USER.md", conversation_id=f"conv-{i}")

    assert store.tracked_conversations() <= 64, (
        f"the snapshot cache grew to {store.tracked_conversations()} conversations"
    )


def test_the_MOST_RECENT_conversations_survive_eviction(store, monkeypatch) -> None:  # noqa: ANN001
    """Evicting the conversation currently running would restore the original bug."""
    calls = {"n": 0}

    def _fake_render(entries):  # noqa: ANN001,ANN202
        calls["n"] += 1
        return f"render-{calls['n']}"

    monkeypatch.setattr(store, "_render", _fake_render)
    monkeypatch.setattr(store, "entries", lambda target: [])

    newest = store.snapshot_for_prompt("USER.md", conversation_id="conv-newest")
    for i in range(3):
        store.snapshot_for_prompt("USER.md", conversation_id=f"conv-other-{i}")
    assert store.snapshot_for_prompt("USER.md", conversation_id="conv-newest") == newest
