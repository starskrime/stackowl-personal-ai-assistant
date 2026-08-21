"""C-6 invariant lock — an unwired channel cannot ESCALATE, updated to the real rule.

THIS LOCK FIRED AND WAS IGNORED, which is the part worth knowing. The file used to
assert that a channel with no prompter denies EVERYTHING, and said it existed so that
"a future refactor that made it fail OPEN (privilege escalation by channel) is caught
immediately". Commit 7e020cd1 ("stop blocking agents on a permission nobody was asked
for") made exactly that refactor, the lock went red exactly as designed, and it was
left red rather than answered. It has been failing ever since — found 2026-08-21 while
running a neighbouring suite.

THE ESCALATION IT FEARED IS REAL AND IS ALREADY CLOSED, separately. `AutonomousPrompter`
granted everything at first, and its own docstring admitted it: "THE DOCSTRING ABOVE WAS
ASPIRATIONAL, NOT TRUE ... execute_code -> allowed=True, 'destructive' -> allowed=True,
'lock' -> allowed=True". That was fixed on 2026-08-19 by refusing whenever
``allow_relaxation`` is False.

So the CONTRACT NARROWED rather than reversing, and this file now locks the narrow
version, which is the one that actually protects anything:

  * an unwired channel GRANTS an ordinary consequential action — deliberate, so an
    unattended agent is not blocked on a permission nobody could have been asked for;
  * an unwired channel still REFUSES an always-ask action — the privilege-escalation
    invariant, unchanged;
  * a wired channel is consulted, so the gate is possible and not merely safe.

The old assertions are REPLACED, not deleted, and the reason is written down — a lock
that is quietly relaxed teaches the next reader nothing.
"""

from __future__ import annotations

import pytest

from stackowl.tools.consent import (
    ConsentRequest,
    ConsentScope,
    RoutingPrompter,
)


class _GrantingPrompter:
    """A stub prompter that always grants — proves the router consults it."""

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        return ConsentScope.ONCE


def _req(channel: str, *, always_ask: bool = False) -> ConsentRequest:
    """``allow_relaxation`` is the policy's own signal: it sets it to ``not excluded``,
    so False means the tool or category is on the always-ask list."""
    return ConsentRequest(
        tool_name="shell", channel=channel, session_key="s",
        summary="x", allow_relaxation=not always_ask,
    )


@pytest.mark.asyncio
async def test_an_unwired_channel_cannot_escalate_an_ALWAYS_ASK_action() -> None:
    """THE INVARIANT THIS FILE EXISTS FOR, and the one that still holds.

    An always-ask tool or category reaches the prompter (``excluded`` only skips the
    AUTO shortcuts), so without this it would be granted in the one situation where
    nobody can undo it.
    """
    routing = RoutingPrompter()

    assert await routing.prompt(_req("discord", always_ask=True)) == ConsentScope.DENY
    assert await routing.prompt(_req("whatsapp", always_ask=True)) == ConsentScope.DENY


@pytest.mark.asyncio
async def test_an_unwired_channel_GRANTS_an_ordinary_action() -> None:
    """The deliberate change (7e020cd1). Denying here blocked unattended agents on a
    permission nobody was ever asked for — Bakir, 2026-08-16: "agent was blocked due to
    ask permission and permission was never asked from user". Locked so that reverting
    it is a decision rather than a drift."""
    routing = RoutingPrompter()

    assert await routing.prompt(_req("discord")) == ConsentScope.ONCE


@pytest.mark.asyncio
async def test_registered_channel_is_consulted() -> None:
    """A registered prompter IS consulted — the gate is possible, not just safe."""
    routing = RoutingPrompter()
    routing.register("discord", _GrantingPrompter())  # type: ignore[arg-type]

    assert await routing.prompt(_req("discord")) == ConsentScope.ONCE
    # An unwired channel still cannot escalate an always-ask action.
    assert await routing.prompt(_req("whatsapp", always_ask=True)) == ConsentScope.DENY
