"""ESC-67 — the learned prompt block must stop moving mid-conversation.

`stable_context` is learned preferences plus learned lessons, composed into the
FROZEN half of the system prompt. Learning is exactly what changes, so the frozen
half was not frozen: 203 of 259 measured "prompt part CHANGED" warnings name it,
more than every other part combined.
"""

from __future__ import annotations

from stackowl.pipeline.stable_context_snapshot import (
    StableContextSnapshot,
    shared_stable_context_snapshot,
)


def test_the_block_is_taken_ONCE_per_incarnation() -> None:
    """A lesson learned mid-conversation must not move the cached prefix."""
    snap = StableContextSnapshot()
    first = snap.get("conv-1::secretary", "prefs: A")
    second = snap.get("conv-1::secretary", "prefs: A and a NEW lesson")

    assert first == "prefs: A"
    assert second == "prefs: A", "the learned block moved mid-conversation"


def test_a_NEW_incarnation_picks_up_what_was_learned() -> None:
    """Freezing is per incarnation — the next /new is exactly where D01.1 permits
    the prompt to change, and is where learning is supposed to land."""
    snap = StableContextSnapshot()
    snap.get("conv-1::secretary", "prefs: A")
    assert snap.get("conv-2::secretary", "prefs: A and B") == "prefs: A and B"


def test_the_SAME_lane_with_a_DIFFERENT_OWL_is_separate() -> None:
    """D01.6: one lane can run several owls and each must have its own prompt.
    Keying on the lane alone would hand the second owl the first owl's block."""
    snap = StableContextSnapshot()
    snap.get("conv-1::secretary", "secretary prefs")
    assert snap.get("conv-1::verifier", "verifier prefs") == "verifier prefs"


def test_an_EMPTY_block_is_not_frozen() -> None:
    """Empty is the normal cold state before anything is recalled. Freezing it
    would pin a whole conversation to "no learned context" if the first turn
    happened to precede any recall."""
    snap = StableContextSnapshot()
    assert snap.get("conv-1::secretary", "") == ""
    assert snap.get("conv-1::secretary", "prefs: A") == "prefs: A"


def test_the_map_is_bounded() -> None:
    snap = StableContextSnapshot(max_tracked=3)
    for i in range(10):
        snap.get(f"conv-{i}::secretary", f"prefs {i}")
    assert snap.tracked() == 3


def test_the_shared_instance_is_one_per_process() -> None:
    assert shared_stable_context_snapshot() is shared_stable_context_snapshot()
