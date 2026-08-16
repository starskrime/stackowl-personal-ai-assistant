"""The router must judge a message WITH the conversation it belongs to.

BAKIR'S REPORT, 2026-08-16: "when I'm asking my agent to change something on his
schedule, he asked me to approve. If I already said do it, it's already do it."

WHAT THE LOGS SHOWED. Not the consent gate — `cronjob` declares
`action_severity="write"`, and ConsequentialActionGate only asks on
"consequential", so it never ran. The turn was routed `intent_class=clarify` by
the LLM router, and `_maybe_clarify` then surfaced a question and yielded the
turn without acting. The exchange:

    user:      B retime it
    assistant: What does "B" refer to, and what specifically needs to be retimed?

THE CAUSE. `router.route()` built its prompt from `state.input_text` ALONE —
`messages = [Message(role="user", content=prompt)]`. `state.history` is populated
by the CLASSIFY step, which runs AFTER triage, so at routing time there was
nothing to resolve "B" or "it" against. In isolation that message genuinely is
ambiguous; in context it is not. The router was answering the wrong question.

NOT A PROMPT PROBLEM. The clarify guidance was already emphatic — last resort,
prefer standard, act when reversible — and adding words would have been tuning to
the example. The router simply lacked the data.

SCALE, so nobody reads this as routine: ZERO of 433 routed turns in the preceding
fortnight (2026-08-01..15) were classified clarify. It is rare and expensive — it
costs a whole turn and makes the agent look like it forgot what was just said.
"""

from __future__ import annotations

import stackowl.owls.router as router_module

_ROUTER = next(
    getattr(router_module, n)
    for n in dir(router_module)
    if isinstance(getattr(router_module, n), type) and n.endswith("Router")
)
_OWLS = [("secretary", "general assistant"), ("Brain", "research")]
_RECENT = (
    "can you change Brain's check-in schedule?\n"
    "Brain currently runs its check-in at 09:00 daily."
)


def _prompt(user_text: str, recent: str = "") -> str:
    return _ROUTER.__new__(_ROUTER)._build_prompt(_OWLS, user_text, recent)


class TestTheConversationReachesTheRouter:
    def test_recent_turns_appear_in_the_prompt(self) -> None:
        out = _prompt("B retime it", _RECENT)

        assert "Recent conversation" in out
        assert "check-in schedule" in out

    def test_the_reference_and_the_request_are_both_present(self) -> None:
        """The router can only resolve "it" if both halves are in front of it."""
        out = _prompt("B retime it", _RECENT)

        assert "B retime it" in out
        assert "09:00" in out


class TestItIsToldWhatToDoWithThem:
    def test_it_is_told_to_resolve_references_before_judging(self) -> None:
        out = _prompt("B retime it", _RECENT)

        assert "resolve what the request refers to" in out

    def test_a_short_follow_up_is_declared_NOT_ambiguous(self) -> None:
        """The specific instruction that answers Bakir's report."""
        out = _prompt("B retime it", _RECENT)

        assert "SHORT FOLLOW-UP" in out
        assert "not ambiguous" in out

    def test_it_is_told_not_to_re_confirm_an_instruction(self) -> None:
        """His words: "if I already said do it, it's already do it"."""
        out = _prompt("B retime it", _RECENT)

        assert "already told you to do something" in out
        assert "do not ask them to confirm it again" in out


class TestNoHistoryChangesNothing:
    def test_the_block_is_absent_without_recent_turns(self) -> None:
        """A first turn, or a failed history read, must see the prompt the router
        has always seen — the fallback is silence, not a placeholder."""
        out = _prompt("B retime it")

        assert "Recent conversation" not in out
        assert "resolve what the request refers to" not in out

    def test_the_request_is_still_there(self) -> None:
        assert "B retime it" in _prompt("B retime it")


class TestTheClarifyGuidanceSurvived:
    def test_clarify_is_still_a_last_resort(self) -> None:
        """The additions must not have displaced the guidance that was already
        there — this is an addition, not a rewrite."""
        out = _prompt("anything", _RECENT)

        assert "LAST RESORT" in out
        assert "choose 'standard' and act" in out
