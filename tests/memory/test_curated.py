"""D08.1 slice 1 — the two curated files, their budget, and consolidation.

The tests that matter are the ones about REFUSING, because refusing is the
mechanism: a budget that never binds is a budget that teaches the agent nothing,
and the 88,631-fact store this replaces is what an unbounded write path produces.

The other load-bearing one is the frozen snapshot. D01.1 measured per-turn recall
varying in every session observed, which silently forfeits the provider's prefix
cache, so the prompt must not move mid-session even though the file does.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import (
    DURABILITIES,
    ENTRY_DELIMITER,
    MAX_CONSOLIDATION_FAILURES_PER_TURN,
    USER_BUDGET_CHARS,
    USER_TARGET,
    CuratedMemory,
    Entry,
)


@pytest.fixture
def mem(tmp_path):
    return CuratedMemory(root=tmp_path / "memory")


# --------------------------------------------------------------------------- #
# Writing.
# --------------------------------------------------------------------------- #


def test_an_entry_round_trips(mem):
    res = mem.add(USER_TARGET, "Bakir prefers root-cause fixes.", "permanent")

    assert res.ok is True
    assert res.done is True
    entries = mem.entries(USER_TARGET)
    assert [e.text for e in entries] == ["Bakir prefers root-cause fixes."]
    assert entries[0].durability == "permanent"


def test_the_file_is_readable_by_a_human(mem, tmp_path):
    """The whole reason this is a file and not a table (D08.1 R7Q27)."""
    mem.add(USER_TARGET, "Runs on a Jetson.", "permanent")

    text = (tmp_path / "memory" / "USER.md").read_text()

    assert "Runs on a Jetson." in text
    assert "[permanent]" in text


def test_the_success_response_does_not_echo_the_entries(mem):
    """The reference platform observed the model treating an echoed list as an
    invitation to 'find more to fix' and re-issuing the same write five times."""
    mem.add(USER_TARGET, "One.", "permanent")

    res = mem.add(USER_TARGET, "Two.", "permanent")

    assert res.entries == []
    assert res.done is True


def test_a_duplicate_is_a_no_op_not_an_error(mem):
    mem.add(USER_TARGET, "Same thing.", "permanent")

    res = mem.add(USER_TARGET, "Same thing.", "permanent")

    assert res.ok is True
    assert len(mem.entries(USER_TARGET)) == 1


def test_empty_content_is_refused(mem):
    assert mem.add(USER_TARGET, "   ", "permanent").ok is False


# --------------------------------------------------------------------------- #
# Durability. There is deliberately no 'transient'.
# --------------------------------------------------------------------------- #


def test_transient_is_not_an_accepted_durability(mem):
    """THE test for the stale-date failure. The most-reinforced entry in the
    88,631-fact store this replaces was "Today's date is 2026-07-15", three
    weeks old and reinforced 157 times. A durability that can express "this
    expires" will accumulate expired things, so there isn't one."""
    res = mem.add(USER_TARGET, "Today's date is 2026-07-15.", "transient")

    assert res.ok is False
    assert "transient" in res.message
    assert mem.entries(USER_TARGET) == []


def test_an_unknown_durability_is_refused(mem):
    assert mem.add(USER_TARGET, "x", "forever").ok is False


@pytest.mark.parametrize("durability", DURABILITIES)
def test_both_real_durabilities_are_stored_in_the_entry(mem, durability):
    """Stored, not merely validated — eviction sorts on it, so it has to survive
    the round trip."""
    mem.add(USER_TARGET, "A thing.", durability)

    assert mem.entries(USER_TARGET)[0].durability == durability


def test_a_hand_edited_entry_without_a_marker_still_loads(mem, tmp_path):
    """A user who deletes the [permanent] marker while editing has written a
    perfectly reasonable line. Treating that as corruption would punish the
    editing this design exists to allow."""
    path = tmp_path / "memory" / "USER.md"
    path.parent.mkdir(parents=True)
    path.write_text("just a plain line\n", encoding="utf-8")

    entries = mem.entries(USER_TARGET)

    assert [e.text for e in entries] == ["just a plain line"]
    assert entries[0].durability == "permanent"


# --------------------------------------------------------------------------- #
# The budget, and the consolidation protocol it drives.
# --------------------------------------------------------------------------- #


def _fill(mem, target=USER_TARGET):
    """Fill to just under the cap with entries the agent could plausibly write."""
    i = 0
    while True:
        text = f"Fact number {i} about the user and how they work."
        if mem.used_chars(target) + len(text) + len(ENTRY_DELIMITER) + 14 > mem.budget_for(target):
            return i
        assert mem.add(target, text, "permanent").ok
        i += 1


def test_a_write_over_budget_is_refused(mem):
    _fill(mem)

    res = mem.add(USER_TARGET, "One more thing that will not fit at all here.", "permanent")

    assert res.ok is False
    assert mem.used_chars(USER_TARGET) <= USER_BUDGET_CHARS


def test_the_refusal_tells_the_model_what_to_do_and_shows_it_the_entries(mem):
    """Unlike the success path, the over-capacity path MUST echo entries — the
    model needs them to choose what to merge or drop."""
    _fill(mem)

    res = mem.add(USER_TARGET, "Another fact that does not fit in the budget.", "permanent")

    assert res.done is False, "the model should retry in this turn"
    assert "consolidate" in res.message.lower()
    assert res.entries, "it cannot consolidate what it cannot see"


def test_consolidating_then_retrying_succeeds(mem):
    """The protocol working end to end: refuse, the agent removes, the retry
    lands. This is what makes forgetting the agent's problem."""
    _fill(mem)
    blocked = mem.add(USER_TARGET, "A newly learned preference worth keeping.", "permanent")
    assert blocked.ok is False

    mem.remove(USER_TARGET, "Fact number 0")
    mem.remove(USER_TARGET, "Fact number 1")

    assert mem.add(USER_TARGET, "A newly learned preference worth keeping.", "permanent").ok


def test_repeated_failures_go_terminal_so_the_turn_can_finish(mem):
    """A failed memory side effect must never suppress the user's reply."""
    _fill(mem)
    text = "Something that will never fit no matter how many times we try it."

    results = [
        mem.add(USER_TARGET, text, "permanent")
        for _ in range(MAX_CONSOLIDATION_FAILURES_PER_TURN + 1)
    ]

    assert all(r.ok is False for r in results)
    assert results[-1].done is True, "must stop asking the model to retry"
    assert "skipped" in results[-1].message.lower()


def test_a_successful_write_resets_the_failure_budget(mem):
    """The cap counts CONSECUTIVE failures, not lifetime ones — otherwise a
    successful consolidation mid-turn would still be punished.

    THE INPUT CHANGED IN D08.4, THE SUBJECT DID NOT. This probe used to be
    ``"x" * (USER_BUDGET_CHARS + 10)``. D08.4 added a per-entry ceiling
    (``MAX_ENTRY_BUDGET_FRACTION``) because a single write could otherwise evict
    the whole store, and that ceiling now catches an entry that large BEFORE the
    capacity path — so the old probe stopped reaching the behaviour under test.

    It is answered with a terminal refusal on purpose: no amount of consolidation
    can make an entry larger than the ceiling fit, so asking the model to
    consolidate would be asking for something that provably cannot work. The probe
    below is under the ceiling and over the remaining budget, which is exactly the
    case the consolidation protocol exists for. Verified against the live class:
    ``done`` is still ``False`` here, so the reset invariant holds unchanged.
    """
    _fill(mem)
    mem.add(USER_TARGET, "Does not fit here at all, not even close.", "permanent")
    mem.remove(USER_TARGET, "Fact number 0")
    assert mem.add(USER_TARGET, "Short one.", "permanent").ok

    probe = "y" * 400
    assert len(probe) <= mem._max_entry_chars(USER_TARGET), "probe must clear the ceiling"
    res = mem.add(USER_TARGET, probe, "permanent")

    assert res.ok is False
    assert res.done is False, "the counter should have reset on the success"


def test_an_entry_over_the_per_entry_ceiling_is_terminal_not_a_consolidation_ask(mem):
    """The other half of the split above, pinned so neither drifts.

    Consolidation frees space by dropping OTHER entries. It can never make one
    oversized entry fit, so the refusal is terminal and the message tells the model
    to split it rather than to consolidate.
    """
    res = mem.add(USER_TARGET, "x" * (USER_BUDGET_CHARS + 10), "permanent")

    assert res.ok is False
    assert res.done is True, "consolidation cannot help; do not ask for it"
    assert "consolidat" not in res.message.lower()


def test_reset_turn_clears_the_budget(mem):
    _fill(mem)
    for _ in range(MAX_CONSOLIDATION_FAILURES_PER_TURN + 1):
        mem.add(USER_TARGET, "nope, too big to fit in here", "permanent")

    mem.reset_turn()
    res = mem.add(USER_TARGET, "still too big to fit in here", "permanent")

    assert res.done is False


def test_the_owl_budget_is_larger_than_the_user_budget(mem):
    assert mem.budget_for("scout") > mem.budget_for(USER_TARGET)


# --------------------------------------------------------------------------- #
# Replace and remove.
# --------------------------------------------------------------------------- #


def test_replace_swaps_one_entry(mem):
    mem.add(USER_TARGET, "Bakir uses npm.", "until_changed")

    res = mem.replace(USER_TARGET, "npm", "Bakir uses uv.", "until_changed")

    assert res.ok is True
    assert [e.text for e in mem.entries(USER_TARGET)] == ["Bakir uses uv."]


def test_replace_reports_a_miss_and_shows_what_is_there(mem):
    mem.add(USER_TARGET, "Something.", "permanent")

    res = mem.replace(USER_TARGET, "nothing like this", "New.", "permanent")

    assert res.ok is False
    assert res.entries


def test_remove_drops_the_entry(mem):
    mem.add(USER_TARGET, "Temporary interest in X.", "until_changed")

    assert mem.remove(USER_TARGET, "Temporary interest").ok is True
    assert mem.entries(USER_TARGET) == []


# --------------------------------------------------------------------------- #
# The frozen snapshot — Law 1.
# --------------------------------------------------------------------------- #


def test_the_snapshot_does_not_move_mid_session(mem):
    """THE prompt-stability test. A write lands on disk immediately and is
    visible to the tool, but the prompt keeps what it started with."""
    mem.add(USER_TARGET, "First.", "permanent")
    before = mem.snapshot_for_prompt(USER_TARGET, conversation_id="incarnation-1")

    mem.add(USER_TARGET, "Second, written mid-session.", "permanent")

    assert mem.snapshot_for_prompt(USER_TARGET, conversation_id="incarnation-1") == before
    assert "Second" not in before
    assert any("Second" in e.text for e in mem.entries(USER_TARGET)), "but disk has it"


def test_a_new_incarnation_picks_up_the_write(mem):
    """What /new is for. Measured caveat, accepted in R3Q9: the main Telegram
    lane has had two incarnations in its life, so this can be a long wait."""
    mem.add(USER_TARGET, "First.", "permanent")
    mem.snapshot_for_prompt(USER_TARGET, conversation_id="incarnation-1")
    mem.add(USER_TARGET, "Second.", "permanent")

    after = mem.snapshot_for_prompt(USER_TARGET, conversation_id="incarnation-2")

    assert "Second." in after


def test_the_write_confirmation_says_when_it_takes_effect(mem):
    """Or the agent appears to ignore what it just learned."""
    res = mem.add(USER_TARGET, "A thing.", "permanent")

    assert "/new" in res.message


def test_an_absent_file_snapshots_as_empty(mem):
    assert mem.snapshot_for_prompt(USER_TARGET, conversation_id="s1") == ""


# --------------------------------------------------------------------------- #
# Targets and paths.
# --------------------------------------------------------------------------- #


def test_owl_notes_are_separate_from_the_user_profile(mem):
    mem.add(USER_TARGET, "About the user.", "permanent")
    mem.add("scout", "About doing my job.", "permanent")

    assert [e.text for e in mem.entries(USER_TARGET)] == ["About the user."]
    assert [e.text for e in mem.entries("scout")] == ["About doing my job."]


def test_two_owls_do_not_share_a_budget(mem):
    """A shared bounded file would have owls silently evicting each other."""
    mem.add("scout", "Scout's note.", "permanent")
    mem.add("brain", "Brain's note.", "permanent")

    assert len(mem.entries("scout")) == 1
    assert len(mem.entries("brain")) == 1


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "with space", "x" * 80])
def test_a_target_that_could_escape_the_directory_is_refused(mem, bad):
    """Owl names are user-chosen and this builds a path from one."""
    with pytest.raises(ValueError):
        mem.path_for(bad)


def test_entries_are_stored_under_the_voted_directory(mem, tmp_path):
    mem.add(USER_TARGET, "x", "permanent")
    mem.add("scout", "y", "permanent")

    assert (tmp_path / "memory" / "USER.md").exists()
    assert (tmp_path / "memory" / "scout.md").exists()


def test_a_partial_write_cannot_truncate_the_profile(mem, tmp_path, monkeypatch):
    """Written via a temp file and replaced, so a crash mid-write cannot leave a
    truncated profile that the next boot reads as the user's whole identity."""
    mem.add(USER_TARGET, "Important and long-standing fact.", "permanent")
    original = (tmp_path / "memory" / "USER.md").read_text()

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.replace", _boom)
    with pytest.raises(OSError):
        mem.add(USER_TARGET, "Second fact.", "permanent")

    assert (tmp_path / "memory" / "USER.md").read_text() == original


def test_entry_rendering_round_trips_through_parse():
    entry = Entry(text="Something [with] brackets.", durability="until_changed")

    assert Entry.parse(entry.rendered()) == entry


# --------------------------------------------------------------------------- #
# The nudge (D08.3, pulled into this item).
# --------------------------------------------------------------------------- #


def test_the_nudge_is_silent_until_the_interval():
    """It must be rare. A reminder on every turn is noise the model learns to
    ignore, and the user pays for it in every reply."""
    from stackowl.memory.curated import NUDGE_INTERVAL_TURNS, note_turn, reset_nudges

    reset_nudges()
    early = [note_turn("lane-a") for _ in range(NUDGE_INTERVAL_TURNS - 1)]

    assert early == [None] * (NUDGE_INTERVAL_TURNS - 1)


def test_the_nudge_fires_at_the_interval():
    """Without it nothing prompts a write at all, now that extraction is gone —
    'when the agent decides to', measured across a real week, is zero."""
    from stackowl.memory.curated import NUDGE_INTERVAL_TURNS, note_turn, reset_nudges

    reset_nudges()
    for _ in range(NUDGE_INTERVAL_TURNS - 1):
        note_turn("lane-a")

    assert note_turn("lane-a") is not None


def test_the_nudge_tells_the_agent_not_to_write_nothing():
    """An empty note is worse than none — it costs budget and says nothing."""
    from stackowl.memory.curated import NUDGE_INTERVAL_TURNS, note_turn, reset_nudges

    reset_nudges()
    for _ in range(NUDGE_INTERVAL_TURNS - 1):
        note_turn("lane-a")
    text = note_turn("lane-a")

    assert text is not None
    assert "say nothing" in text


def test_the_counter_resets_after_firing():
    from stackowl.memory.curated import NUDGE_INTERVAL_TURNS, note_turn, reset_nudges

    reset_nudges()
    for _ in range(NUDGE_INTERVAL_TURNS):
        note_turn("lane-a")

    assert note_turn("lane-a") is None, "it should not fire twice in a row"


def test_a_write_resets_the_counter():
    from stackowl.memory.curated import (
        NUDGE_INTERVAL_TURNS,
        note_turn,
        note_write,
        reset_nudges,
    )

    reset_nudges()
    for _ in range(NUDGE_INTERVAL_TURNS - 1):
        note_turn("lane-a")

    note_write("lane-a")

    assert note_turn("lane-a") is None, "it just wrote; it needs no reminder"


def test_lanes_are_counted_independently():
    """One busy conversation must not nudge a different, quiet one."""
    from stackowl.memory.curated import NUDGE_INTERVAL_TURNS, note_turn, reset_nudges

    reset_nudges()
    for _ in range(NUDGE_INTERVAL_TURNS):
        note_turn("lane-a")

    assert note_turn("lane-b") is None
