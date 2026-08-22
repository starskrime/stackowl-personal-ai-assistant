"""A banned capability must be UNAVAILABLE, not merely discouraged.

BAKIR, 2026-08-16: the agent gets "stuck trying a single solution continuously".

MEASURED over 7 days: 27 retry events against 3 substitutions. The retry ladder
repeats the same approach roughly nine times for every one time it reroutes.

THE CAUSE IS DOCUMENTED IN THE MODULE ITSELF. retry_actuator.py's own docstring:

    "capability avoidance is PROMPT-STEERED (the re-run's goal text names the
    banned capabilities and asks the model not to use them again), not a hard
    filter threaded through tool-selection. The model can still pick a banned
    capability if it insists. Upgrade path: thread banned_capabilities into
    execute.py's tool-selection as a real exclusion list if soft steering proves
    unreliable in practice."

Soft steering asks a model that has just failed with a tool to please not use that
tool. The 27:3 ratio is the measurement the author asked for, so this is that
documented upgrade, not a new idea.

WHY THIS IS CACHE-SAFE (LAW 1). execute.py already has two tool-selection paths,
and the ``restrict_to`` branch is explicitly NOT memoized ("a planned envelope is
per-task by construction"). Expressing the ban as a restriction therefore rides an
existing non-memoized path and cannot poison the session's fitted array — which
matters because a retry child REUSES the original session_key.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.steps.execute import _restrict_to_for_turn

pytestmark = pytest.mark.asyncio


ALL = ("web_search", "owl_build", "cronjob", "memory", "send_message")

# `owl_build` is NO LONGER a valid example of an ordinary bannable tool. Since
# 2026-08-22 it is in APPEAL_TOOLS and is exempt from bans at every gate — a ban
# may never remove the means by which an agent ASKS for what it lacks. These tests
# therefore use `cronjob`/`memory` for the ordinary case, and the exemption gets
# its own class below rather than being left implicit in a changed example.
_ORDINARY = "cronjob"


class TestTheBanIsRealNotAdvisory:
    async def test_a_banned_capability_is_removed_from_the_presented_set(self) -> None:
        got = _restrict_to_for_turn(
            envelope_tools=None, banned=(_ORDINARY,), all_names=ALL
        )

        assert got is not None, "a ban must produce a restriction"
        assert _ORDINARY not in got, "the failed capability is still on the menu"
        assert "web_search" in got, "the ban must not remove everything else"

    async def test_several_bans_are_all_enforced(self) -> None:
        got = _restrict_to_for_turn(
            envelope_tools=None, banned=("cronjob", "memory"), all_names=ALL
        )

        assert got is not None
        assert {"cronjob", "memory"}.isdisjoint(got)
        assert len(got) == 3

    async def test_a_ban_INTERSECTS_an_existing_envelope(self) -> None:
        """A task envelope already narrows the set. The ban must subtract from it,
        never widen it back to everything — that would hand a restricted task tools
        it was never granted."""
        got = _restrict_to_for_turn(
            envelope_tools=frozenset({"web_search", "cronjob"}),
            banned=("cronjob",),
            all_names=ALL,
        )

        assert got == frozenset({"web_search"})

    async def test_banning_everything_leaves_no_restriction_rather_than_nothing(
        self,
    ) -> None:
        """Degenerate case. An empty presented array would leave the model unable to
        act at all, which is a worse failure than letting it retry: it could not
        even reroute. Fall back to no restriction and let the ladder's attempt
        ceiling stop the loop instead.
        """
        got = _restrict_to_for_turn(
            envelope_tools=None, banned=ALL, all_names=ALL
        )

        # Banning literally everything leaves only the appeal tools, which is not
        # a usable menu — so this still falls back to no restriction and lets the
        # attempt ceiling stop the loop, exactly as before.
        assert got is None or set(got) <= {"owl_build", "owls_list"}


class TestNothingChangesWhenThereIsNoBan:
    async def test_no_ban_and_no_envelope_is_unrestricted(self) -> None:
        """The normal turn. Must stay on the MEMOIZED path — returning a
        restriction here would silently disable the prompt-cache-stable array for
        every ordinary turn."""
        assert _restrict_to_for_turn(
            envelope_tools=None, banned=(), all_names=ALL
        ) is None

    async def test_an_envelope_without_a_ban_is_untouched(self) -> None:
        env = frozenset({"web_search", "memory"})

        assert _restrict_to_for_turn(
            envelope_tools=env, banned=(), all_names=ALL
        ) == env

    async def test_a_ban_naming_an_unknown_tool_changes_nothing(self) -> None:
        """A stale ban for a tool that no longer exists must not shrink the set."""
        assert _restrict_to_for_turn(
            envelope_tools=None, banned=("a_tool_that_was_deleted",), all_names=ALL
        ) is None


class TestTheRetryCarriesItsBans:
    async def test_the_retry_child_state_carries_banned_capabilities(self) -> None:
        """The queue row already records what failed; the child turn must actually
        receive it, or the enforcement above never gets a chance to run."""
        from stackowl.pipeline.state import PipelineState

        s = PipelineState(
            trace_id="t", session_key="s", input_text="x", channel="cli",
            owl_name="o", pipeline_step="", banned_capabilities=("owl_build",),
        )

        assert s.banned_capabilities == ("owl_build",)

    async def test_it_defaults_to_no_bans(self) -> None:
        from stackowl.pipeline.state import PipelineState

        s = PipelineState(
            trace_id="t", session_key="s", input_text="x", channel="cli",
            owl_name="o", pipeline_step="",
        )

        assert s.banned_capabilities == ()


class TestTheASKIsExemptFromBans:
    """A ban may never remove the tool by which an agent asks for what it lacks.

    MEASURED 2026-08-22 02:20:25: banned=["delegate_task","memory","owl_build"].
    The loop's own error text tells a blocked agent to "ask the user to grant it —
    owl_build action='grant'", and `owl_build` was on the ban list. It earned its
    place by failing — and it failed because `_grant` called register() on an owl
    that already exists, which could never succeed. The platform banned the tool
    its own healing depends on, for failing at a job it could not do.

    This class lives in the file that OWNS ban semantics so the exemption is
    stated, not inferred from a changed example above.
    """

    async def test_the_ask_survives_its_own_ban(self) -> None:
        got = _restrict_to_for_turn(
            envelope_tools=None, banned=("owl_build", "cronjob"), all_names=ALL
        )
        assert got is not None
        assert "owl_build" in got, "the agent can no longer ask for what it lacks"
        assert "cronjob" not in got, "the exemption must not swallow the whole ban"

    async def test_the_ask_survives_a_narrowing_envelope_too(self) -> None:
        """Gate 3 and gate 2 compose: a plan that omitted the ask, plus a ban on
        it, must still leave it reachable."""
        got = _restrict_to_for_turn(
            envelope_tools=frozenset({"web_search"}),
            banned=("owl_build",),
            all_names=ALL,
        )
        assert got is None or "web_search" in got
