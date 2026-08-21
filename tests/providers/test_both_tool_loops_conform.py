"""The executable divergence inventory for the two tool loops.

Read `tests/providers/conformance.py` first — it explains the harness and why this is
diagnostic rather than a change.

HOW TO READ THIS FILE. Two kinds of test live here and they mean opposite things:

  * `TestTheyAgree...` — behaviour an extraction MUST preserve. A failure here means the
    two loops just diverged somewhere they used to match.
  * `TestTheyDiverge...` — behaviour that differs TODAY, recorded as an expectation with
    the divergence it demonstrates. A failure here means someone changed one side. That
    is not necessarily bad — it may be the fix — but it must be noticed, which is the
    whole point. Nothing here asserts the divergence is correct.

The agreement tests come first deliberately: a harness that cannot show the two loops
matching on the simple cases has not earned the right to be believed about the hard
ones.
"""

from __future__ import annotations

import pytest

from stackowl.providers.llm_gateway import ESCALATE_SENTINEL
from tests.providers.conformance import DIALECTS, Call, ReActCall, Say, drive

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _not_test_mode(monkeypatch):
    from stackowl.config.test_mode import TestModeGuard

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


# --------------------------------------------------------------------------- #
# What must stay true. These are the extraction's acceptance criteria.
# --------------------------------------------------------------------------- #


class TestTheyAgreeOnTheOrdinaryPath:
    @pytest.mark.parametrize("dialect", DIALECTS)
    async def test_a_straight_answer_is_returned_unchanged(self, dialect: str) -> None:
        out = await drive(dialect, [Say("here is your answer")])

        assert out.text == "here is your answer"
        assert out.tool_calls == 0

    async def test_a_straight_answer_agrees_across_dialects(self) -> None:
        a = await drive("openai", [Say("same")])
        b = await drive("anthropic", [Say("same")])

        assert a.comparable() == b.comparable()

    async def test_one_tool_call_then_an_answer_agrees(self) -> None:
        script = [Call("web_search", {"query": "q"}), Say("done")]

        a = await drive("openai", script)
        b = await drive("anthropic", script)

        assert a.text == b.text == "done"
        assert a.tool_calls == b.tool_calls == 1
        assert a.comparable() == b.comparable()

    async def test_the_tool_is_dispatched_before_the_answer_on_both(self) -> None:
        """Ordering is behaviour. If an extraction reorders dispatch relative to the
        final answer, every tool result silently arrives one turn late."""
        script = [Call("web_search", {"query": "q"}), Say("done")]

        for dialect in DIALECTS:
            out = await drive(dialect, script)
            assert "dispatch:web_search" in out.events, (dialect, out.events)

    async def test_several_tool_calls_are_all_recorded_on_both(self) -> None:
        script = [Call("web_search", {"q": "1"}), Call("web_search", {"q": "2"}),
                  Say("finished")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert a.tool_calls == b.tool_calls == 2
        assert a.text == b.text == "finished"


# --------------------------------------------------------------------------- #
# What differs today. Each records ONE divergence from D04.1's brainstorm.
# --------------------------------------------------------------------------- #


class TestTheyAgreeOnEscalation:
    """D1 and D2 — CLOSED 2026-08-21 (ESC-21). Was `TestTheyDivergeOnEscalation`.

    Both objective escalation signals used to exist on the OpenAI side only:
    ESCALATE_SENTINEL when the tier burns its whole tool budget without delivering,
    and again when the persistence judge rules give-up. Anthropic had neither — it
    went to wrap-up, and it nudged.

    Bakir's answer to ESC-21 was to close the divergences in place rather than extract
    the shared loop, so these expectations INVERT: the two must now agree. The hazard
    they were pinning is what made that the right order — if a shared loop had been
    written from the Anthropic shape first, OpenAI would have silently lost both, with
    no failing test outside `test_auto_escalate.py` and no live signal except a
    WARNING that stops appearing.
    """

    async def test_budget_exhaustion_escalates_on_BOTH(self) -> None:
        script = [Call("web_search", {"q": "x"})]

        openai = await drive("openai", script, loop_last=True,
                             can_escalate=True, max_iterations=3)
        anthropic = await drive("anthropic", script, loop_last=True,
                                can_escalate=True, max_iterations=3)

        assert openai.text == ESCALATE_SENTINEL, "openai regressed on D1"
        assert anthropic.text == ESCALATE_SENTINEL, "anthropic does not escalate (D1)"

    async def test_a_judge_ruled_give_up_escalates_on_BOTH(self) -> None:
        async def _judge(_text: str, _calls: list[str]) -> str | None:
            return "You gave up. Try again."

        script = [Say("i cannot do that")]

        openai = await drive("openai", script, loop_last=True, can_escalate=True,
                             max_iterations=3, persistence_check=_judge)
        anthropic = await drive("anthropic", script, loop_last=True, can_escalate=True,
                                max_iterations=3, persistence_check=_judge)

        assert openai.text == ESCALATE_SENTINEL, "openai regressed on D2"
        assert anthropic.text == ESCALATE_SENTINEL, "anthropic does not escalate (D2)"

    async def test_the_judge_still_NUDGES_at_the_ceiling_on_both(self) -> None:
        """The half that must NOT change. With no stronger tier, a give-up verdict
        still nudges rather than escalating — that is what the 3-round cap bounds."""
        rounds = {"openai": 0, "anthropic": 0}

        for dialect in DIALECTS:
            async def _judge(_t: str, _c: list[str], _d: str = dialect) -> str | None:
                rounds[_d] += 1
                return "You gave up. Try again."

            out = await drive(dialect, [Say("i cannot")], loop_last=True,
                              can_escalate=False, max_iterations=6,
                              persistence_check=_judge)
            assert out.text != ESCALATE_SENTINEL, dialect

        assert rounds["openai"] == rounds["anthropic"] == 3, rounds

    async def test_at_the_CEILING_neither_escalates(self) -> None:
        """The half they DO agree on, and it matters: `can_escalate=False` must
        produce an answer, never the sentinel, on both. A sentinel delivered to a
        user is a raw control token."""
        script = [Call("web_search", {"q": "x"})]

        for dialect in DIALECTS:
            out = await drive(dialect, script, loop_last=True,
                              can_escalate=False, max_iterations=3)
            assert out.text != ESCALATE_SENTINEL, dialect
            assert out.text.strip(), f"{dialect} returned an empty floor"


class TestTheyDivergeOnCallbackAccounting:
    """D5 — MEASURED, and narrower than the brainstorm recorded it.

    It does NOT affect the native tool-call path: there both loops emit exactly
    ``dispatch, callback, callback`` for one call. It appears only on the TEXT-ReAct
    (``ACTION:``) path, and it is two separate differences:

      1. Anthropic fires ``on_iteration_complete`` an EXTRA time per iteration.
         One ReAct call: openai 2, anthropic 3. Two ReAct calls: openai 3,
         anthropic 5 — the gap widens per iteration, so anything counting
         iterations from this callback reads a different number for one transcript.
      2. The ORDER around dispatch is inverted. OpenAI dispatches the tool and
         THEN calls back; Anthropic calls back and THEN dispatches.

    (2) is the one with teeth. The callback is how the pipeline raises
    ``TurnStopped`` / ``BudgetBreach``. On OpenAI the tool has ALREADY RUN when that
    lands, so a user stop or a budget kill executes one extra tool; on Anthropic it
    does not. The W4.T17 comment block asserting this invariant is byte-identical on
    both sides, and one of them violates it.

    Recorded as measurements, not as a verdict. Which order is correct is an open
    question, and an extraction must not settle it by accident.
    """

    async def test_the_native_path_AGREES_on_both_count_and_order(self) -> None:
        """The control. Without this, the divergence below could be an artifact of
        the harness rather than of the loops."""
        script = [Call("web_search", {"q": "1"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert a.events == b.events == ["dispatch:web_search", "callback", "callback"]

    async def test_the_text_react_path_fires_an_extra_callback_on_anthropic(
        self,
    ) -> None:
        script = [ReActCall("web_search", {"q": "1"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert (a.callbacks, b.callbacks) == (2, 3), (a.callbacks, b.callbacks)

    async def test_the_gap_WIDENS_with_each_iteration(self) -> None:
        """Not a constant offset a consumer could correct for."""
        script = [ReActCall("web_search", {"q": "1"}),
                  ReActCall("web_search", {"q": "2"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert (a.callbacks, b.callbacks) == (3, 5), (a.callbacks, b.callbacks)

    async def test_dispatch_and_callback_are_ordered_OPPOSITELY(self) -> None:
        """The one with teeth: a stop raised from the callback lands after the tool
        already ran on one side and before it on the other."""
        script = [ReActCall("web_search", {"q": "1"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert a.events[0] == "dispatch:web_search", a.events
        assert b.events[0] == "callback", b.events


class TestTheyAgreeOnTheJudgeNudgeCap:
    """Checked because the brainstorm did NOT claim it and an extraction could break
    it silently. The give-up nudge is budget-REFUNDED on both sides
    (`iter_budget.refund("give_up_directive")`), so nothing in the iteration budget
    bounds it — which would be an unbounded spend if a separate cap did not exist.

    MEASURED: a judge that vetoes every candidate gets exactly 3 rounds on BOTH,
    regardless of `max_iterations` (4, 8 and 16 all give 3). The cap is real, shared,
    and independent of the budget. Both then deliver the vetoed text rather than
    nothing, which is the never-empty floor working as designed.
    """

    @pytest.mark.parametrize("max_iterations", [4, 8, 16])
    @pytest.mark.parametrize("dialect", DIALECTS)
    async def test_an_always_vetoing_judge_is_bounded_at_three_rounds(
        self, dialect: str, max_iterations: int
    ) -> None:
        rounds = 0

        async def _judge(_text: str, _calls: list[str]) -> str | None:
            nonlocal rounds
            rounds += 1
            return "You gave up. Try again."

        out = await drive(dialect, [Say("i cannot help")], loop_last=True,
                          can_escalate=False, max_iterations=max_iterations,
                          persistence_check=_judge)

        assert rounds == 3, f"{dialect}: nudge cap moved to {rounds}"
        assert out.wire_calls == 3, f"{dialect}: {out.wire_calls} wire calls"
