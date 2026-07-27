"""D01.1 slice 4 — the focus clock stops depending on assemble seeding it.

THE COUPLING. `assemble` called FOCUS_TRACKER.begin_turn() inside the per-query
skill-scoring block, and `skill_view` called current_turn() to read the counter
without advancing it. D01.1 removes that scoring block — and with it the only
caller that ever advanced the clock. `_focus_score` returns 0.0 whenever
`current_turn - last_turn < 1`, so a frozen clock makes focus scoring silently
return zero for everything: the tool the design leans on as THE fallback for
removed skill bodies would quietly get worse.

THE FIX (Bakir, 2026-07-27: "skill_view stops depending on an assemble-seeded
counter"). The clock advances when it sees a NEW trace id for that (owl,
session), so ANY caller can drive it and it still ticks exactly once per turn
regardless of who calls first, or whether assemble calls at all. That removes the
ordering dependency rather than moving it somewhere else.

It is also more honest than what it replaces: the clock now advances on actual
skill activity rather than on every assemble, so decay measures turns in which
skills were involved.
"""

from __future__ import annotations

from stackowl.skills.skill_focus import SkillFocusTracker

OWL = "secretary"
LANE = "owl:secretary:telegram:dm:1"


def test_the_clock_advances_once_per_trace_however_often_it_is_asked() -> None:
    """The property that removes the ordering dependency: several callers in one
    turn must see ONE turn, not one each."""
    t = SkillFocusTracker()

    first = t.turn_for(OWL, LANE, "trace-1")
    again = t.turn_for(OWL, LANE, "trace-1")
    third = t.turn_for(OWL, LANE, "trace-1")

    assert first == again == third


def test_a_new_trace_is_a_new_turn() -> None:
    t = SkillFocusTracker()

    one = t.turn_for(OWL, LANE, "trace-1")
    two = t.turn_for(OWL, LANE, "trace-2")

    assert two == one + 1


def test_it_works_with_no_seeding_at_all() -> None:
    """The actual regression guard. assemble no longer calls begin_turn, so the
    very first thing to touch the clock may be skill_view — and decay must work
    from there."""
    t = SkillFocusTracker()

    turn = t.turn_for(OWL, LANE, "trace-1")

    assert turn >= 1, "an unseeded clock must still start a real turn, not 0"


def test_decay_is_measurable_across_turns_without_assemble() -> None:
    """End to end: mark a skill viewed on one turn, advance by trace only, and
    the focus score must decay — which is the behaviour that silently died if
    the clock stayed frozen."""
    from stackowl.skills.skill_focus import _decayed

    t = SkillFocusTracker()
    turn1 = t.turn_for(OWL, LANE, "trace-1")
    t.mark_viewed(OWL, LANE, "pdf", turn1)
    turn2 = t.turn_for(OWL, LANE, "trace-2")

    assert _decayed(1.0, turn1, turn2) > 0.0, "the very next turn still scores"
    assert _decayed(1.0, turn1, turn1) == 0.0, "same turn is not a decay step"


def test_separate_lanes_keep_separate_clocks() -> None:
    t = SkillFocusTracker()

    t.turn_for(OWL, LANE, "trace-1")
    t.turn_for(OWL, LANE, "trace-2")
    other = t.turn_for(OWL, "another-lane", "trace-2")

    assert other == 1, "a different lane starts its own clock"
