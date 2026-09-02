"""An unattended agent is not stopped by a permission nobody was asked for.

BAKIR, 2026-08-16, twice: "disable all approvals for agents", and then the sharper
version — "agent was blocked due to ask permission and permission was never asked
from user".

WHAT THE LOGS SHOWED. 28 refusals of "tool_build.execute: no user present to
approve — refused (fail closed)", plus the same shape from owl_build. Both tools
checked `interactive` and refused BEFORE consulting the consent policy at all, so:

  * the user was never asked — there was no prompt to answer, so from their side
    the agent simply failed;
  * the tools' own reasoning never ran. The comment directly beneath that check
    argues a learned tool is REVERSIBLE (action='delete' unregisters it) and
    should auto-proceed with undo instead of prompting every time.

And beneath that, RoutingPrompter denied whenever a turn had no channel UX — which
is precisely the autonomous case, not a dangerous one.

WHAT IS DELIBERATELY NOT RELAXED. ConsentPolicy applies the always-ask tools
(execute_code, computer_use, ha_call_service, browser_dialog) and categories
(lock, alarm, destructive, prompt_surface) BEFORE any prompter is consulted, so
none of this touches them. Those are what the E11-E13 reviews refused to relax and
what ESC-1 added because such writes change what the agent WILL DO on later turns.
Widening to them is a separate, explicit decision — these tests pin that it has
not happened by accident.
"""

from __future__ import annotations

import pytest

from stackowl.tools.consent import (
    AutonomousPrompter,
    ConsentRequest,
    ConsentScope,
    RoutingPrompter,
)

pytestmark = pytest.mark.asyncio


def _req(tool: str = "tool_build", channel: str = "", category: str | None = None) -> ConsentRequest:
    return ConsentRequest(
        tool_name=tool,
        channel=channel,
        session_key="owl:secretary:telegram:dm:1",
        category=category,
        summary=f"Register new tool {tool}",
        reversible=True,
    )


class TestTheAutonomousGrant:
    async def test_it_grants_rather_than_denying(self) -> None:
        assert await AutonomousPrompter().prompt(_req()) is ConsentScope.ONCE

    async def test_it_grants_ONCE_not_a_standing_permission(self) -> None:
        """A grant nobody witnessed must not silently become a session-wide or
        permanent one — the next action gets the same scrutiny."""
        scope = await AutonomousPrompter().prompt(_req())

        assert scope is ConsentScope.ONCE
        assert scope is not ConsentScope.SESSION

    async def test_the_grant_is_logged_at_INFO(self) -> None:
        """Production runs at INFO. An approval nobody saw must be one anybody can
        find afterwards, or the audit trail has a hole exactly where consent was
        skipped."""
        import logging

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        logger = logging.getLogger("stackowl.tool")
        handler = _Capture(level=logging.INFO)
        prev = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            await AutonomousPrompter().prompt(_req())
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev)

        grants = [r for r in records if "autonomous grant" in r.getMessage()]
        assert grants, [r.getMessage() for r in records]
        assert grants[0].levelno == logging.INFO
        assert getattr(grants[0], "_fields", {}).get("tool") == "tool_build"


class TestTheRouterNoLongerDeniesUnasked:
    async def test_a_turn_with_no_channel_UX_is_granted_not_denied(self) -> None:
        """The autonomous case: nobody can be asked, so denying is not the safe
        answer — it is no answer, and the task dies unattended."""
        assert await RoutingPrompter().prompt(_req()) is not ConsentScope.DENY

    async def test_a_registered_channel_still_asks_its_own_prompter(self) -> None:
        """The whole point is that a REAL user still gets asked. If a channel UX
        exists, it decides — the autonomous path must not shadow it."""
        asked: list[str] = []

        class _Prompter:
            async def prompt(self, req: ConsentRequest) -> ConsentScope:
                asked.append(req.tool_name)
                return ConsentScope.DENY

        router = RoutingPrompter()
        router.register("telegram", _Prompter())

        scope = await router.prompt(_req(channel="telegram"))

        assert asked == ["tool_build"], "the channel's own prompter was bypassed"
        assert scope is ConsentScope.DENY, "a real user's refusal must still stand"


class TestTheRingfenceHolds:
    """These are policy-level, not prompter-level: ConsentPolicy applies them
    before any prompter runs, so the autonomous grant can never see them."""

    async def test_the_always_ask_tools_are_what_the_operator_decided(self) -> None:
        """`execute_code` LEFT this set on 2026-09-02, by Bakir's decision, and
        this test changed with it rather than being deleted.

        The ringfence existed because E11/E12/E13 said code execution is never
        relaxed. What that review did not know: `shell` launched a general-purpose
        interpreter 110 times out of 153 (72%) unattended with no prompt, in the
        same logs where `execute_code` was refused 26 times. The gate stopped
        nothing — anything refused here ran one line later through `shell`.

        Asked as a risk-appetite question, because evidence cannot settle it:
        gate the shell path too, or relax this one. He chose relax. The three
        tools that remain are the ones nothing else can reach around."""
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_TOOLS

        assert _DEFAULT_ALWAYS_ASK_TOOLS == frozenset(
            {"computer_use", "ha_call_service", "browser_dialog"}
        )
        assert "execute_code" not in _DEFAULT_ALWAYS_ASK_TOOLS

    async def test_the_always_ask_categories_never_shrink(self) -> None:
        """The invariant is that the ringfence never LOSES a category — not that it
        can never gain one. Exact equality also blocked STRENGTHENING it, which is
        how "authority_widening" (2026-08-19) tripped this test while making the
        platform stricter, not looser."""
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_CATEGORIES

        assert frozenset(
            {"lock", "alarm", "destructive", "prompt_surface"}
        ) <= _DEFAULT_ALWAYS_ASK_CATEGORIES

    async def test_granting_an_owl_a_capability_is_ringfenced(self) -> None:
        """A capability granted forever is the least reversible action here, so it
        must reach a human or not happen."""
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_CATEGORIES

        assert "authority_widening" in _DEFAULT_ALWAYS_ASK_CATEGORIES
