"""96 refusals, 48 in one day, and the refusal was right every time.

`tool refused by bounds` has fired **96 times** across every retained log — 48 on
2026-09-10 alone — and **59 of them sit on `retry-*` traces**. jobmarket was
refused `todo` 35 times and `shell` 15; mailbutler and syshealth account for the
rest. Every one of those refusals was correct: the tool is outside the owl's
declared bounds and `bounds_guard` blocked it before consent, fail-closed.

What was wrong is that the model was SHOWN the tool, asked for it, and was
refused — over and over. `execute.py` already knew the cost and had written it
down in `_record_capability_gap`:

    "mailbutler was refused `shell` 24 TIMES — it needs the tool, asks on every
    run, is refused, reports honestly, and starts again from nothing the next
    run. That is what 'my agents keep failing at their jobs' looks like from
    inside."

THE CAUSE IS ONE COPY OF A RULE THE SAME FILE STATES CORRECTLY ELSEWHERE.
`_restrict_to_for_turn` says of the envelope case: *"An envelope is INTERSECTED,
never replaced: a task granted three tools must not get the whole registry back
because one of them was banned."* Its BAN branch does not follow that rule — it
builds the remainder from `all_names`, the whole registry minus the bans, which
for a bounded owl is a superset of what the owl may call.

That alone would be harmless, because presentation is narrowed to the owl's
effective bounds a few lines later. But that narrowing was guarded on
`restrict_to is None`, so setting a ban restriction DISABLED it. The ban path
therefore widened the presented set past the owl's bounds and then skipped the
one thing that would have narrowed it back.

The narrowing's own comment records the same defect being fixed once before, for
a different trigger — headhunter self-reporting `web_search` as present yet always
refused, live on 2026-07-23. It was fixed for the case that surfaced, with a guard
that left every other case open.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.steps.execute import (
    _narrow_to_effective_bounds,
    _restrict_to_for_turn,
)

#: A bounded owl's allowlist, and a registry far wider than it.
_BOUNDS = frozenset({"web_search", "send_message", "memory"})
_ALL = ("web_search", "send_message", "memory", "shell", "todo", "execute_code")


class TestTheNarrowing:
    @pytest.mark.tripwire
    def test_a_ban_restriction_is_narrowed_back_to_the_owls_bounds(self) -> None:
        """THE DEFECT, end to end through both functions.

        A retry bans one tool. `_restrict_to_for_turn` returns the whole registry
        minus that ban — which still contains `shell`, `todo` and `execute_code`,
        none of which this owl may call. The narrowing must bring it back.
        """
        after_ban = _restrict_to_for_turn(
            envelope_tools=None, banned=("execute_code",), all_names=_ALL
        )
        assert after_ban is not None and "shell" in after_ban, (
            "the ban path no longer widens past the bounds — re-read this guard "
            "rather than deleting it"
        )

        presented = _narrow_to_effective_bounds(after_ban, _BOUNDS)

        assert presented == _BOUNDS - {"execute_code"} or presented == _BOUNDS, (
            f"presented {sorted(presented or ())} is not within the owl's bounds"
        )
        for out_of_bounds in ("shell", "todo", "execute_code"):
            assert out_of_bounds not in (presented or ()), (
                f"{out_of_bounds!r} would be presented to an owl whose bounds "
                "forbid it — dispatch will refuse it and the model will ask again"
            )

    @pytest.mark.tripwire
    def test_an_unbounded_owl_is_left_exactly_as_it_was(self) -> None:
        """Byte-for-byte control. Eight of eleven owls carry no tools bound at all,
        and narrowing must be a no-op for them or this change is a behaviour change
        for the majority of the platform."""
        assert _narrow_to_effective_bounds(None, None) is None
        keep = frozenset({"shell"})
        assert _narrow_to_effective_bounds(keep, None) is keep

    @pytest.mark.tripwire
    def test_bounds_apply_when_nothing_else_restricts(self) -> None:
        """The original 2026-07-23 headhunter case, which must keep working."""
        assert _narrow_to_effective_bounds(None, _BOUNDS) == _BOUNDS

    @pytest.mark.tripwire
    def test_an_empty_intersection_keeps_the_prior_restriction(self) -> None:
        """`_restrict_to_for_turn` already settled this for bans: "a model with no
        tools cannot reroute either." An empty menu is worse than a menu dispatch
        will refuse, because refusal at least produces a capability-gap record."""
        disjoint = frozenset({"shell", "todo"})

        assert _narrow_to_effective_bounds(disjoint, _BOUNDS) == disjoint

    @pytest.mark.tripwire
    def test_it_never_widens(self) -> None:
        """The invariant that makes intersection safe wherever it is reached. Stated
        as a property rather than an example, because the value of intersection is
        that it cannot widen no matter which caller reaches it."""
        for restrict in (None, frozenset({"web_search"}), frozenset(_ALL)):
            for bounds in (None, _BOUNDS, frozenset()):
                got = _narrow_to_effective_bounds(restrict, bounds)
                if restrict is None or got is None:
                    continue
                assert got <= restrict or got == bounds, (
                    f"narrowing widened {sorted(restrict)} to {sorted(got)}"
                )


class TestTheSeamStillCallsIt:
    """Built-but-not-wired, on the function this whole item exists to wire."""

    @pytest.mark.tripwire
    def test_execute_narrows_presentation_unconditionally(self) -> None:
        """The previous version was guarded on `restrict_to is None`, which is
        exactly how the ban path escaped it. A guard that returns to a conditional
        call site rebuilds the defect, so the call must be unconditional."""
        import inspect
        import textwrap

        from stackowl.pipeline.steps import execute as mod

        src = textwrap.dedent(inspect.getsource(mod._run_with_tools))  # type: ignore[attr-defined]
        assert "_narrow_to_effective_bounds(" in src, (
            "the dispatch path no longer narrows presentation to the owl's bounds"
        )
        assert "if restrict_to is None and profile is None:" not in src, (
            "the conditional narrowing is back — a ban or an envelope will disable "
            "it again, which is the defect this item fixed"
        )
        assert "if profile is None:" in src, (
            "the profile exclusion was dropped. Presentation gives restrict_to "
            "absolute precedence and DROPS profile groups, so narrowing a profiled "
            "owl silently swaps its DNA capability groups for its bounds. Zero of "
            "eleven owls carry a profile, so that cannot be measured here — widening "
            "to it needs evidence, not tidiness."
        )
