"""C-6 invariant lock — a channel with NO consent prompter fails CLOSED.

The whole F004/F005 ordering rests on this: wiring Discord/WhatsApp into startup
is only safe because ``RoutingPrompter.prompt`` ALREADY denies when a channel has
no registered prompter. This test locks that invariant so a future refactor that
made it fail OPEN (privilege escalation by channel) is caught immediately.

It also asserts the positive: a channel WITH a registered prompter is consulted
(so the gate is genuinely possible, not just safe-but-unusable).
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


def _req(channel: str) -> ConsentRequest:
    return ConsentRequest(
        tool_name="shell", channel=channel, session_id="s",
        summary="x", allow_relaxation=True,
    )


@pytest.mark.asyncio
async def test_unregistered_channel_denies() -> None:
    """No prompter registered for the channel → DENY (fail closed, never open)."""
    routing = RoutingPrompter()
    # Nothing registered for 'discord' / 'whatsapp'.
    assert await routing.prompt(_req("discord")) == ConsentScope.DENY
    assert await routing.prompt(_req("whatsapp")) == ConsentScope.DENY


@pytest.mark.asyncio
async def test_registered_channel_is_consulted() -> None:
    """A registered prompter IS consulted — the gate is possible, not just safe."""
    routing = RoutingPrompter()
    routing.register("discord", _GrantingPrompter())  # type: ignore[arg-type]
    assert await routing.prompt(_req("discord")) == ConsentScope.ONCE
    # An OTHER channel with no prompter still fails closed.
    assert await routing.prompt(_req("whatsapp")) == ConsentScope.DENY
