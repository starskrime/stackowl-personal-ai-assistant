"""Bakir must be able to say yes, and the platform must not say yes for him.

BAKIR, 2026-08-19: "I cannot create an agent... I'm giving my permission to do that.
Agent still failing. I'm giving everything agent still failing. What's our core
issue?"

THE WHOLE DAY, from the logs, in order:

    17:10–19:28  nine consent denials (send_file, claude_code) — he was never asked
    23:10:48     create mailbutler → "you already have 5 agent-created owls (cap 5)"
    23:11:07     the agent RETIRED his sysfup owl to make room
    23:11:21     mailbutler created
    23:11:30     edit to add `shell` → missing: ['shell'] → overclaim.detected
    23:14:59     edit → "declined by user — owl 'mailbutler' was not built."
    23:15:12     edit → "declined by user"
    23:19:30     edit → "declined by user"
    02:30:12     edit → "declined by user"
    02:32:41     edit → "declined by user"

He never declined anything. The prompt could not parse the session key, failed
closed, and the platform recorded ``reason: 'user_denied'`` and told him he had
refused. It denied on his behalf and then blamed him for it.

MEASURED THE OTHER WAY, and this is the part that inverts the whole model — a
policy with no channel UX (the unattended case)::

    execute_code       -> allowed=True
    'destructive'      -> allowed=True
    'lock'             -> allowed=True

Those are precisely the tools and categories the E11–E13 reviews refused to relax.
``AutonomousPrompter``'s docstring asserts "ConsentPolicy applies the always-ask
tool and category sets BEFORE any prompter is consulted, so execute_code,
computer_use, ha_call_service, browser_dialog and the lock / alarm / destructive /
prompt_surface categories are untouched." That is not what the code does:
``excluded`` only skips the AUTO shortcuts, and the request still reaches the
prompter — which, with no channel registered, grants it.

SO THE AUTHORIZATION WAS INVERTED. Attended: denied, and blamed on him. Unattended:
everything granted, including the ringfenced set. Exactly backwards from both the
design and what a person would want.

``allow_relaxation`` already carries the fact a prompter needs: the policy sets it
to ``not excluded``, so ``False`` means "this is always-ask". The autonomous grant
now honours it, which makes the docstring's claim true instead of aspirational.
"""

from __future__ import annotations

import pytest

from stackowl.tools.consent import (
    AutonomousPrompter,
    ConsentPolicy,
    ConsentRequest,
    ConsentScope,
    RoutingPrompter,
)

pytestmark = pytest.mark.asyncio


class TestTheRingfencedSetIsNeverGrantedWithNobodyWatching:
    async def test_execute_code_is_not_autonomously_granted(self) -> None:
        """MEASURED as allowed=True before this. The unattended case is exactly
        where nobody can undo it."""
        allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
            tool_name="execute_code", channel="cron", session_key="s1",
            summary="", reversible=False,
        )

        assert allowed is False

    async def test_a_destructive_category_is_not_autonomously_granted(self) -> None:
        allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
            tool_name="anything", channel="cron", session_key="s1",
            category="destructive", summary="", reversible=False,
        )

        assert allowed is False

    async def test_authority_widening_is_never_autonomous(self) -> None:
        """Granting an owl a capability FOREVER is the least reversible thing the
        platform can do. It must reach a human or not happen."""
        allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
            tool_name="owl_build", channel="cron", session_key="s1",
            category="authority_widening", summary="", reversible=True,
        )

        assert allowed is False


class TestOrdinaryAutonomousWorkIsStillUnblocked:
    """The autonomous grant exists for a real reason (Bakir, 2026-08-16: "agent was
    blocked due to ask permission and permission was never asked from user"). Fixing
    the hole must not re-block the ordinary unattended work it was built for."""

    async def test_a_normal_consequential_tool_is_still_granted(self) -> None:
        allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
            tool_name="send_file", channel="cron", session_key="s1",
            summary="", reversible=False,
        )

        assert allowed is True

    async def test_the_prompter_itself_still_grants_a_relaxable_request(self) -> None:
        scope = await AutonomousPrompter().prompt(ConsentRequest(
            tool_name="send_file", channel="cron", session_key="s1",
            allow_relaxation=True,
        ))

        assert scope is ConsentScope.ONCE

    async def test_the_prompter_refuses_an_always_ask_request(self) -> None:
        """allow_relaxation=False is how the policy already marks always-ask."""
        scope = await AutonomousPrompter().prompt(ConsentRequest(
            tool_name="execute_code", channel="cron", session_key="s1",
            allow_relaxation=False,
        ))

        assert scope is ConsentScope.DENY
