"""C-6 invariant lock — an unwired channel cannot ESCALATE, updated to the real rule
(Story 3.4 narrows it further: an unwired channel now DENIES outright).

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

WHY IT NARROWED AGAIN (Story 3.4). The 7e020cd1 fix routed an unwired channel to
``AutonomousPrompter`` — deliberate, so an unattended agent was not blocked on a
permission nobody could have been asked for. But "no channel UX" conflated two
different situations under one signal: a genuinely unattended trigger (a scheduled
job — nobody CAN be asked), and a live channel the operator simply failed to wire a
prompter for (somebody COULD be asked, and the platform never tried). The fallback
granted BOTH. Unattended work now carries an EXPLICIT, trigger-set ``principal``
(``autonomous:scheduler``) on ``TraceContext``, read by ``ConsentPolicy.request()``
BEFORE it ever calls ``RoutingPrompter`` — so the genuinely unattended case no longer
needs "no prompter registered" to stand in for it, and ``RoutingPrompter`` itself can
go back to denying unconditionally, PLUS open a durable ``incident`` needs_you item so
a human finds the wiring fault.

So the contract is, once again:

  * an unwired channel DENIES an ordinary consequential action, and opens one
    deduplicated ``incident`` needs_you item for that channel — the fallback to
    ``AutonomousPrompter`` is deleted outright;
  * an unwired channel still REFUSES an always-ask action — the privilege-escalation
    invariant, unchanged;
  * a wired channel is consulted, so the gate is possible and not merely safe.

The old assertions are REPLACED, not deleted, and the reason is written down — a lock
that is quietly relaxed teaches the next reader nothing.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
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
async def test_an_unwired_channel_DENIES_an_ordinary_action_and_opens_ONE_incident(
    tmp_db: DbPool,
) -> None:
    """Story 3.4 — the ``AutonomousPrompter`` fallback (7e020cd1) is deleted outright.

    "No channel UX" used to be treated as proof nobody could be asked, and denying
    there DID block unattended agents on a permission nobody was ever asked for
    (Bakir, 2026-08-16: "agent was blocked due to ask permission and permission was
    never asked from user") — but the fix conflated that case with a live channel
    whose prompter simply was not wired. Unattended work now proves itself with an
    explicit ``principal`` on ``TraceContext`` instead, so ``RoutingPrompter`` can go
    back to denying unconditionally — and now also opens exactly ONE durable
    ``incident`` per channel (called twice, deduplicated), so the wiring fault is
    never silent either."""
    routing = RoutingPrompter(db_pool=tmp_db)

    assert await routing.prompt(_req("discord")) == ConsentScope.DENY
    assert await routing.prompt(_req("discord")) == ConsentScope.DENY

    rows = await tmp_db.fetch_all(
        "SELECT * FROM needs_you WHERE kind = 'incident' AND resolved_cursor IS NULL"
    )
    assert len(rows) == 1, "two denials on the same channel must open exactly ONE item"


@pytest.mark.asyncio
async def test_registered_channel_is_consulted() -> None:
    """A registered prompter IS consulted — the gate is possible, not just safe."""
    routing = RoutingPrompter()
    routing.register("discord", _GrantingPrompter())  # type: ignore[arg-type]

    assert await routing.prompt(_req("discord")) == ConsentScope.ONCE
    # An unwired channel still cannot escalate an always-ask action.
    assert await routing.prompt(_req("whatsapp", always_ask=True)) == ConsentScope.DENY
