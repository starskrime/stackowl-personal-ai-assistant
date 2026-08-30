"""The model cannot plan with a tool it is never offered.

Bakir, 2026-08-29: "in today flow i do not see the plan steps which will plan
actions and evaluate." He is right, and the reason is not that planning was never
built — it is that planning is never PRESENTED.

MEASURED across every retained log:

    `todo` invocations, ever ................ 4
    `update_plan` invocations, ever ......... 1
    web_search 1,785        web_fetch 1,678

and, decisively:

    "eligible tools NOT presented" lines .... 869
    of which dropped `todo` ................. 869   (100%)
    of which dropped `update_plan` .......... 869   (100%)

So on every turn that hit the cap, the plan tools were not on the menu. Four
invocations is not a model that ignores planning; it is a model that could not
reach it.

WHY THEY AND NOT SOMETHING ELSE. The drop list is
`sessions_spawn, set_output_preference, task_status, todo, transcripts, tts,
undo_write, update_plan, vision_analyze, wait` — the alphabetical tail. The
registry's own comment says so: "rank order for a tool with neither a usage score
nor a declared priority is the ALPHABET". `todo` and `update_plan` sort near the
end and lose every time. Nothing decided planning was unimportant; the alphabet did.

THIS IS A KNOWN SHAPE IN THIS FILE. The base set already carries a fix with the
same description: "adds the two skill DISCOVERY tools (skills_list / skill_view)
to the base set so a weak/small-window model can always discover and load a skill
(skill_manage authoring was already base, but discovery was prunable — the bug)."
Planning has no base member at all, so the whole capability is prunable.

The cap is bumped in lockstep, per the convention this file states and follows for
every previous base addition, so guaranteeing planning does NOT shrink the
discretionary headroom an owl profile already had.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import _DEFAULT_BASE, _DEFAULT_CAP


def test_the_plan_tools_are_in_the_guaranteed_base_set() -> None:
    """The fix. A tool that is evicted 869 times out of 869 is not offered."""
    assert "todo" in _DEFAULT_BASE, (
        "`todo` is prunable, so the platform's ability to plan is prunable — it "
        "was dropped on 869 of 869 capped turns"
    )
    assert "update_plan" in _DEFAULT_BASE


def test_the_cap_was_bumped_in_LOCKSTEP() -> None:
    """This file's own convention, stated at every previous base addition:
    "the cap is bumped in lockstep with each base addition so base growth does
    NOT shrink the discretionary per-turn headroom a full owl profile already
    had." Two tools added, so two slots added."""
    assert _DEFAULT_CAP >= 38, (
        f"cap is {_DEFAULT_CAP}; adding 2 base tools without bumping it steals 2 "
        "discretionary slots from every owl profile"
    )


def test_planning_survives_a_hostile_cap() -> None:
    """The property that matters, asserted against the real selector.

    Not "is in a frozenset" — PRESENTED, when the cap is small enough that the
    alphabetical tail-break would certainly have dropped it.
    """
    from stackowl.tools._infra.presentation import PresentationConfig, ToolPresentation
    from stackowl.tools.base import ToolManifest

    class _T:
        def __init__(self, name: str) -> None:
            self.name = name
            self.manifest = ToolManifest(
                name=name, description=f"{name} does a thing.",
                parameters={"type": "object", "properties": {}},
                action_severity="read",
            )

    names = [
        "aaa_first", "bbb", "ccc", "ddd", "eee", "fff", "ggg", "hhh",
        "todo", "update_plan", "zzz_last",
    ]
    presenter = ToolPresentation(PresentationConfig(cap=4))
    chosen = {
        t.name
        for t in presenter.select(
            all_tools=[_T(n) for n in names],  # type: ignore[list-item]
            profile=None, pins=None, hydrated=None,
        )
    }
    assert "todo" in chosen, (
        f"the real selector still drops todo at a tight cap: {sorted(chosen)}"
    )
    assert "update_plan" in chosen, (
        f"update_plan is still evictable: {sorted(chosen)}"
    )
