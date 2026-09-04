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


# --------------------------------------------------------------------------
# Added 2026-09-04, after the nudge was measured at ZERO firings in 407 turns.
# --------------------------------------------------------------------------


def test_a_turn_COUNTS_even_when_the_tool_is_absent() -> None:
    """The gate must suppress the TEXT, never the COUNT.

    This is the defect that made the nudge unfireable. `note_turn` returned at
    the gate BEFORE reaching the counter, so a turn where `skill_manage` was not
    presented — or where the caller did not pass the set at all — was not merely
    un-nudged, it was INVISIBLE to the interval. Only one of the three provider
    call sites passed the names, so the counter advanced on roughly 25 turns a
    day spread across every lane, and no lane ever reached 10. Measured: 0
    firings against 407 turns and five lanes that each exceeded the interval.
    """
    for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS):
        assert skill_nudge.note_turn("lane", None) is None
    # The interval has passed while the tool was unreachable. The very next turn
    # that CAN reach it must nudge — the debt is not thrown away.
    assert skill_nudge.note_turn("lane", _ALL) is not None


def test_a_suppressed_nudge_does_not_reset_the_lane() -> None:
    """Due-but-unreachable must stay due, not start counting from zero again."""
    for _ in range(skill_nudge.SKILL_NUDGE_INTERVAL_TURNS * 2):
        assert skill_nudge.note_turn("lane", frozenset({"shell"})) is None
    assert skill_nudge.note_turn("lane", _ALL) is not None


def test_EVERY_call_site_passes_the_presented_names() -> None:
    """Counted, not merely present — the old guard could not see this defect.

    It asserted the string `presented_tools=_presented_names(tool_schemas)`
    appeared in the module, which one wired call site satisfies. Two others
    called `_turn_context_prefix(state)` with the schemas one line away and the
    nudge silent, and the guard passed the whole time. A test that can only see
    the first instance of a rule cannot protect the rule.
    """
    import ast
    import inspect

    from stackowl.pipeline.steps import execute

    tree = ast.parse(inspect.getsource(execute))
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_turn_context_prefix"
        and not any(kw.arg == "presented_tools" for kw in node.keywords)
    ]
    assert not bare, (
        "these _turn_context_prefix call sites drop presented_tools, which makes "
        f"the skill nudge permanently silent on their path: lines {bare}"
    )


def test_recording_a_skill_RESETS_the_lane_in_PRODUCTION() -> None:
    """The reset half was wired only from a test.

    `note_skill_written` had exactly one caller in the whole tree and it was this
    suite — so in production the lane was never reset after a skill was recorded
    and the model would be asked again immediately, which is the fastest way to
    teach it to ignore the nudge. The memory half does this correctly from
    `tools/knowledge/memory.py`, and is the control that proves the shape.
    """
    import inspect

    from stackowl.tools.knowledge import skill_manage

    src = inspect.getsource(skill_manage)
    assert "note_skill_written" in src, "the skill write does not reset its lane"
