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


class TestTheyAgreeOnCallbackAccounting:
    """D5's COUNT half — CLOSED 2026-08-21. Was `TestTheyDivergeOnCallbackAccounting`.

    Anthropic fired `on_iteration_complete` an extra time per text-ReAct iteration:
    2 vs 3 for one call, 3 vs 5 for two, a gap that WIDENED rather than being an
    offset a consumer could correct for.

    THE CODE'S OWN COMMENT SETTLED IT, so this needed no ruling. W4.T17 says that
    callback runs "BEFORE the give-up nudge at this FINAL-ANSWER boundary". Anthropic
    fired it before `parse_react_action` had run, so it also fired on text that turned
    out to be a ReAct tool call — a boundary that is not a final answer. OpenAI parses
    first and fires it only on the genuine final-answer branch. Anthropic now does the
    same.

    WHAT THIS DELIBERATELY DOES NOT DECIDE. Both loops now dispatch BEFORE the
    iteration callback on every path, so a cooperative stop lands one tool late on
    both. That was already true of the live OpenAI path; Anthropic's pre-parse callback
    had been pre-empting the tool by accident. Making it uniform RAISES the stakes of
    ESC-25 rather than answering it — the order question is still open, and flipping it
    is now a single change applied to both.
    """

    async def test_the_native_path_agrees_on_both_count_and_order(self) -> None:
        script = [Call("web_search", {"q": "1"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert a.events == b.events == ["dispatch:web_search", "callback", "callback"]

    async def test_the_text_react_path_now_agrees_too(self) -> None:
        script = [ReActCall("web_search", {"q": "1"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert a.callbacks == b.callbacks == 2, (a.callbacks, b.callbacks)
        assert a.events == b.events, (a.events, b.events)

    async def test_the_gap_no_longer_widens(self) -> None:
        """The property that made the old divergence unfixable downstream."""
        script = [ReActCall("web_search", {"q": "1"}),
                  ReActCall("web_search", {"q": "2"}), Say("done")]

        a = await drive("openai", script, max_iterations=6)
        b = await drive("anthropic", script, max_iterations=6)

        assert a.callbacks == b.callbacks == 3, (a.callbacks, b.callbacks)

    async def test_dispatch_precedes_the_callback_on_both_BY_DECISION(self) -> None:
        """ESC-25, ANSWERED 2026-08-21: dispatch first, on both loops, deliberately.

        Was pinned here as an OPEN question. Bakir chose to keep dispatch first, against
        my recommendation — I argued a tool that runs after the user says stop cannot be
        un-run. His counter-case is the one the code already encodes: `ReActIterationState`
        carries `tool_call_records`, so the callback REPORTS a completed iteration, and
        firing it before dispatch would have it report an iteration that has not happened.

        THE CONSEQUENCE IS USER-VISIBLE and is not what "stop" implies: a cooperative
        stop, or a budget kill, lets the tool already requested finish. That is now
        uniform across both loops rather than accidental — anthropic used to pre-empt it
        on the text-ReAct path only, by an ordering nobody had chosen.

        Kept as a test so the behaviour stays deliberate: changing it must fail here
        first, on both sides at once.
        """
        script = [ReActCall("web_search", {"q": "1"}), Say("done")]

        for dialect in DIALECTS:
            out = await drive(dialect, script, max_iterations=6)
            assert out.events[0] == "dispatch:web_search", (dialect, out.events)


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
