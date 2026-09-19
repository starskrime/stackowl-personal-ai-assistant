"""Story 3.4 (AC3, AC5) — the explicit `autonomous:scheduler` principal
bypasses channel-keyed routing entirely, but the six always-ask categories
are still refused under it.

`ConsentPolicy.request()` reads `principal` off `TraceContext` and, when it
equals `PRINCIPAL_AUTONOMOUS_SCHEDULER`, awaits a fresh `AutonomousPrompter()`
directly instead of `self.prompter` (usually `RoutingPrompter`) — so a request
on `channel="internal"`, with NO prompter registered for it anywhere, is
still decided. `AutonomousPrompter`'s own always-ask refusal
(`allow_relaxation is False -> DENY`) is untouched, so this bypass must never
grant any of the six categories the operator ring-fenced.
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.tools.consent import (
    _DEFAULT_ALWAYS_ASK_CATEGORIES,
    PRINCIPAL_AUTONOMOUS_SCHEDULER,
    ConsentPolicy,
    RoutingPrompter,
)

pytestmark = pytest.mark.asyncio


async def test_an_ordinary_tool_is_granted_with_no_prompter_registered_anywhere() -> None:
    token = TraceContext.start(principal=PRINCIPAL_AUTONOMOUS_SCHEDULER)
    try:
        allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
            tool_name="send_file", channel="internal", session_key="job:x",
            summary="", reversible=False,
        )
    finally:
        TraceContext.reset(token)

    assert allowed is True


@pytest.mark.parametrize("category", sorted(_DEFAULT_ALWAYS_ASK_CATEGORIES))
async def test_every_always_ask_category_is_still_refused_under_the_principal(
    category: str,
) -> None:
    """Six categories: lock, alarm, destructive, prompt_surface,
    authority_widening, owl_build. None may be silently run "at once" just
    because the run declared itself autonomous."""
    token = TraceContext.start(principal=PRINCIPAL_AUTONOMOUS_SCHEDULER)
    try:
        allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
            tool_name="some_tool", channel="internal", session_key="job:x",
            category=category, summary="", reversible=False,
        )
    finally:
        TraceContext.reset(token)

    assert allowed is False, f"{category} must never be auto-granted"


async def test_exactly_six_always_ask_categories_are_covered() -> None:
    """A future addition/removal to the ring-fence is a decision, not drift —
    this pins the count the parametrization above assumes."""
    assert frozenset({
        "lock", "alarm", "destructive", "prompt_surface",
        "authority_widening", "owl_build",
    }) == _DEFAULT_ALWAYS_ASK_CATEGORIES


async def test_no_principal_still_denies_on_the_same_unwired_channel() -> None:
    """The contrast that makes the bypass safe: WITHOUT the declared
    principal, the exact same unwired-channel request denies (Story 3.4's
    other headline behaviour) rather than silently falling through to a
    grant."""
    allowed = await ConsentPolicy(prompter=RoutingPrompter()).request(
        tool_name="send_file", channel="internal", session_key="job:x",
        summary="", reversible=False,
    )

    assert allowed is False


async def test_a_registered_channel_prompter_is_still_bypassed_by_the_principal() -> None:
    """The principal routes to AutonomousPrompter REGARDLESS of what channel
    the job happens to carry — even one with a real, registered prompter —
    because the job's channel is set for delivery/scoping, not attendance
    (spec Design Notes)."""
    asked: list[str] = []

    class _WouldAsk:
        async def prompt(self, req: object) -> object:  # pragma: no cover — must not run
            asked.append("asked")
            from stackowl.tools.consent import ConsentScope

            return ConsentScope.DENY

    routing = RoutingPrompter()
    routing.register("telegram", _WouldAsk())  # type: ignore[arg-type]

    token = TraceContext.start(principal=PRINCIPAL_AUTONOMOUS_SCHEDULER)
    try:
        allowed = await ConsentPolicy(prompter=routing).request(
            tool_name="send_file", channel="telegram", session_key="job:x",
            summary="", reversible=False,
        )
    finally:
        TraceContext.reset(token)

    assert allowed is True
    assert asked == [], "the channel's own prompter must be bypassed by the principal"
