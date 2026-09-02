"""D09.4 — asking works; volunteering does not.

THE MEASUREMENT THAT DECIDED THIS ITEM, taken 2026-09-02 across five days of
rotated logs:

    "[curated] nudge: due"    96 fired
    "[curated] add: stored"   35 writes
    nudges followed by a write within 10 minutes: 27 of 96 (28%)
    and 27 of the 35 WRITES — 77% — followed a nudge

THE OBVIOUS READING OF D09.1 IS REFUTED BY THAT. D09.1 measured `evolve_now` and
`synthesize_skills` at ZERO invocations all-time against `note_applied_lesson`'s
791, and concluded that tools asking the model to stop and do meta-work are never
chosen. The first half is right; the inference "so prompting cannot help" is not.
The model will not VOLUNTEER meta-work. Asked at the right moment it complies
about a quarter of the time, and that quarter is where nearly all the writes come
from.

ONE COUNTER, NOT TWO. The mechanism was inline in `memory/curated.py` and is now
`infra/nudge.TurnNudge`, used by both. A second copy of a per-lane counter is the
duplication shape this codebase keeps paying for.

THE GATE IS THE PART THIS PROJECT ALREADY PAID FOR. `skills_list` showed zero
invocations for eight days, and the cause was not that the model ignored it — on
those turns it was NOT PRESENTED. Nudging toward a tool the model cannot reach
would manufacture that failure on purpose, so the nudge is silent unless
`skill_manage` is in the presented set, and silent when the set is unknown.
"""

from __future__ import annotations

import pytest

from stackowl.infra.nudge import TurnNudge
from stackowl.skills import nudge as skill_nudge


@pytest.fixture(autouse=True)
def _clean() -> None:
    skill_nudge.reset()


_ALL = frozenset({"skill_manage", "shell", "read_file"})


def test_it_fires_on_the_interval_and_not_before() -> None:
    """A nudge every turn is noise; one that never fires is decoration."""
    fired = [
        skill_nudge.note_turn("lane", _ALL)
        for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS)
    ]
    assert fired[:-1] == [None] * (skill_nudge.SKILL_NUDGE_INTERVAL_TURNS - 1)
    assert fired[-1] and "skill_manage" in fired[-1]


def test_it_is_SILENT_when_the_tool_is_not_presented() -> None:
    """The `skills_list` lesson, asserted directly: eight days of zero invocations
    because the tool was not there to call. Nudging toward an absent tool would
    manufacture that failure deliberately."""
    for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS * 3):
        assert skill_nudge.note_turn("lane", frozenset({"shell"})) is None


def test_an_UNKNOWN_presented_set_is_silent_too() -> None:
    """Fails closed. Telling the model to use a tool that may not be there is
    worse than staying quiet."""
    for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS * 3):
        assert skill_nudge.note_turn("lane", None) is None


def test_writing_a_skill_RESETS_the_counter() -> None:
    """The agent just did the thing; nudging it again immediately is the fastest
    way to teach it to ignore the nudge."""
    for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS - 1):
        skill_nudge.note_turn("lane", _ALL)
    skill_nudge.note_skill_written("lane")
    assert skill_nudge.note_turn("lane", _ALL) is None


def test_lanes_are_counted_separately() -> None:
    """One chat's turns must not trigger another chat's nudge."""
    for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS - 1):
        skill_nudge.note_turn("a", _ALL)
    assert skill_nudge.note_turn("b", _ALL) is None


def test_its_interval_is_LONGER_than_the_memory_nudge() -> None:
    """Deliberate, not arbitrary: a durable fact appears in most conversations, a
    reusable procedure does not. A nudge that fires when there is nothing to
    record trains the model to ignore it."""
    from stackowl.memory.curated import NUDGE_INTERVAL_TURNS

    assert skill_nudge.SKILL_NUDGE_INTERVAL_TURNS > NUDGE_INTERVAL_TURNS


def test_the_counter_never_raises() -> None:
    """A reminder may never cost a turn its answer."""
    n = TurnNudge(interval=1, text="x", label="[t] nudge")
    assert n.note_turn("") == "x"
    n.note_action("")
    n.reset()


def test_the_memory_nudge_uses_the_SAME_primitive() -> None:
    """One counter, not two — asserted structurally so a later reader does not
    reintroduce the inline copy."""
    import inspect

    from stackowl.memory import curated

    src = inspect.getsource(curated)
    assert "TurnNudge(" in src
    assert "_TURNS_SINCE_WRITE" not in src, "the inline counter is back"


def test_it_is_WIRED_with_the_presented_names() -> None:
    """A nudge nothing calls is decoration, and one called without the presented
    set is permanently silent by its own gate — both are failure modes this
    project has shipped before."""
    import inspect

    from stackowl.pipeline.steps import execute

    src = inspect.getsource(execute)
    assert "skills.nudge import note_turn" in src
    assert "presented_tools=_presented_names(tool_schemas)" in src
