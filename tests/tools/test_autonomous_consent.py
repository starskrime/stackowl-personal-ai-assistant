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

    async def test_the_always_ask_tools_are_unchanged(self) -> None:
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_TOOLS

        assert _DEFAULT_ALWAYS_ASK_TOOLS == frozenset(
            {"execute_code", "computer_use", "ha_call_service", "browser_dialog"}
        )

    async def test_the_always_ask_categories_are_unchanged(self) -> None:
        from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_CATEGORIES

        assert _DEFAULT_ALWAYS_ASK_CATEGORIES == frozenset(
            {"lock", "alarm", "destructive", "prompt_surface"}
        )
