"""Pipeline steps communicate through PipelineState, not through each other.

D02.1 ("One agent class, all surfaces") states this as a measured architectural
property and rests four ``no_change_needed`` stages partly on it::

    cross_step_imports: "ZERO. Every step is `async def run(state) -> state` and
    they communicate ONLY through an immutable PipelineState. There is no coupling
    to unpick because there is none."

RE-MEASURED 2026-08-30 as part of auditing progress.yml's 504 measured-absence
claims for rot: the count is no longer zero. ``execute.py`` imports
``merge_consecutive_roles`` from ``classify.py``. One import is a small crack, but
the recorded claim was absolute and is now false — and nothing was watching it, so
it would have kept drifting.

THE HELPER WAS NEVER A ``classify`` CONCERN. ``merge_consecutive_roles`` is the
D01.5 alternation invariant: a pure function over ``list[Message]`` that collapses
same-role runs because "strict providers reject a messages array with two
consecutive turns of the same role outright". Both ``classify`` and ``execute``
need it before calling a provider. It is shared shaping that happened to be typed
into whichever step needed it first.

This test pins the INVARIANT rather than the function — the function already has
its own suite in test_message_role_alternation.py. A test that only checked the
move would let the next shared helper recreate the coupling silently, which is
exactly how this one appeared.
"""

from __future__ import annotations

import pathlib
import re

_STEPS = pathlib.Path(__file__).resolve().parents[2] / "src/stackowl/pipeline/steps"


def _step_modules() -> set[str]:
    return {p.stem for p in _STEPS.glob("*.py")} - {"__init__"}


def test_no_step_imports_another_step() -> None:
    """D02.1's stated property, measured rather than assumed."""
    mods = _step_modules()
    offenders: list[str] = []
    for path in sorted(_STEPS.glob("*.py")):
        if path.stem == "__init__":
            continue
        src = path.read_text()
        for m in re.finditer(
            r"from stackowl\.pipeline\.steps\.(\w+) import|from \.(\w+) import", src
        ):
            other = m.group(1) or m.group(2)
            if other in mods and other != path.stem:
                offenders.append(f"{path.stem} -> {other}")
    assert not offenders, (
        "a pipeline step imports another step, so they no longer communicate only "
        f"through PipelineState: {sorted(set(offenders))}"
    )


def test_the_shared_helper_has_a_shared_home() -> None:
    """The move, asserted at its destination rather than by its absence."""
    from stackowl.pipeline.message_shaping import merge_consecutive_roles

    assert callable(merge_consecutive_roles)


def test_both_former_callers_still_reach_it() -> None:
    """Neither step lost the capability in the move."""
    from stackowl.pipeline.steps import classify, execute

    assert classify.merge_consecutive_roles is not None
    assert execute.merge_consecutive_roles is not None


def test_the_alternation_invariant_still_holds() -> None:
    """A smoke check that the move did not change behaviour.

    The full suite lives in test_message_role_alternation.py; this only proves the
    relocated function is the same function.
    """
    from stackowl.pipeline.message_shaping import merge_consecutive_roles
    from stackowl.providers.base import Message

    out = merge_consecutive_roles([
        Message(role="assistant", content="a"),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    ])
    assert [m.role for m in out] == ["assistant", "user"]
    assert "a" in out[0].content and "b" in out[0].content
