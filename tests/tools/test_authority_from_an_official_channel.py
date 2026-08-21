"""Freedom when the request came from a channel connected to the gateway.

BAKIR, 2026-08-21: *"I want to provide freedom to them if request come from official
channels."* And, asked what official means: *"if it come from channel which is
connected to gateway."*

THE PROBLEM THAT PRODUCED IT. `authority_widening` is always-ask (2026-08-19), for a
good reason: a capability granted to an owl is permanent and is the least reversible
thing here. But always-ask resolves to REFUSED whenever no human is attached —
`AutonomousPrompter` declines anything with `allow_relaxation=False` — so an owl doing
unattended work could never widen, and the request died silently rather than waiting.

Measured: `owl_build` with `authority_widening` returned False under the autonomous
prompter, on every path.

WHAT "CONNECTED TO THE GATEWAY" BUYS. It is a provenance claim, and it is the one the
platform can actually make. A turn arriving on a live channel adapter came through the
operator's own configured, authenticated ingress — Telegram, CLI, Slack. A turn with
no such origin did not: a scheduled sweep, an MCP caller, a webhook, an internal
sub-goal. The first has an owner behind it and a place to answer; the second has
neither, and is exactly where an unreviewable permanent grant would be worst.

So authority follows the ORIGIN of the request rather than the presence of a human at
the keyboard — which is the authority-vs-action split this platform already paid to
learn, applied to its remaining half.

WHAT THIS DELIBERATELY DOES NOT RELAX. Every other always-ask member is untouched:
execute_code, computer_use, ha_call_service, browser_dialog, and the lock / alarm /
destructive / prompt_surface categories. Those gate what the agent DOES, and an
official origin says nothing about whether a destructive act was intended. Only
`authority_widening` — the question of what an owl may BE — follows provenance.
"""

from __future__ import annotations

import pytest

from stackowl.tools.consent import AutonomousPrompter, ConsentRequest

pytestmark = pytest.mark.asyncio


def _req(*, category: str, channel: str, relax: bool = False) -> ConsentRequest:
    return ConsentRequest(
        tool_name="owl_build" if category == "authority_widening" else "some_tool",
        category=category,
        summary="grant mailbutler send_message",
        channel=channel,
        session_key="owl:mailbutler:telegram:dm:72055773",
        allow_relaxation=relax,
    )


class TestAnOfficialOriginCarriesAuthority:
    @pytest.mark.parametrize("channel", ["telegram", "cli", "slack"])
    async def test_widening_is_granted_from_a_connected_channel(
        self, channel: str, monkeypatch
    ) -> None:
        """The measured case: an owl asks to widen, from a channel the gateway holds."""
        from stackowl.tools import consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"telegram", "cli", "slack"}))
        assert await AutonomousPrompter().prompt(
            _req(category="authority_widening", channel=channel)
        ) != mod.ConsentScope.DENY

    async def test_widening_is_refused_with_no_official_origin(
        self, monkeypatch
    ) -> None:
        """A scheduled sweep, an MCP caller or a webhook has no owner behind it and
        nowhere to answer. A permanent grant taken there is the least reviewable
        thing the platform could do."""
        from stackowl.tools import consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"telegram"}))
        assert await AutonomousPrompter().prompt(
            _req(category="authority_widening", channel="mcp")
        ) == mod.ConsentScope.DENY

    async def test_an_unknown_channel_is_not_official(self, monkeypatch) -> None:
        """Fail closed on anything the gateway does not actually hold — an origin we
        cannot vouch for is not an origin we trust."""
        from stackowl.tools import consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"telegram"}))
        assert await AutonomousPrompter().prompt(
            _req(category="authority_widening", channel="")
        ) == mod.ConsentScope.DENY

    async def test_a_lookup_failure_fails_closed(self, monkeypatch) -> None:
        """If we cannot establish provenance we do not assume it. A registry error
        must never read as 'official'.

        Patches the REGISTRY, not `_gateway_channels` — the first draft replaced the
        guarded function with a raiser, which bypassed the very guard it meant to
        prove. A double that stands in front of the code under test cannot test it.
        """
        from stackowl.channels import registry as reg
        from stackowl.tools import consent as mod

        def _boom() -> object:
            raise RuntimeError("registry gone")

        monkeypatch.setattr(reg.ChannelRegistry, "instance", staticmethod(_boom))
        assert mod._gateway_channels() == frozenset()
        assert await AutonomousPrompter().prompt(
            _req(category="authority_widening", channel="telegram")
        ) == mod.ConsentScope.DENY


class TestEveryOtherAlwaysAskIsUntouched:
    @pytest.mark.parametrize(
        "category", ["destructive", "lock", "alarm", "prompt_surface", "execute_code"]
    )
    async def test_an_official_origin_does_not_relax_acting(
        self, category: str, monkeypatch
    ) -> None:
        """An official origin says who ASKED. It says nothing about whether a
        destructive act was intended, so it must not unlock one."""
        from stackowl.tools import consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"telegram"}))
        assert await AutonomousPrompter().prompt(
            _req(category=category, channel="telegram")
        ) == mod.ConsentScope.DENY

    async def test_ordinary_relaxable_requests_are_unchanged(
        self, monkeypatch
    ) -> None:
        """The existing autonomous grant — anything not always-ask — keeps working
        exactly as before, on any channel."""
        from stackowl.tools import consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset())
        assert await AutonomousPrompter().prompt(
            _req(category="tool_build", channel="whatever", relax=True)
        ) != mod.ConsentScope.DENY


class TestItReadsTheRealRegistry:
    """THE TEST THE OTHERS CANNOT BE, and the reason it exists is a mistake I made.

    Every test above monkeypatches `_gateway_channels`, so none of them touches the
    registry call inside it. The first implementation called
    `ChannelRegistry.instance().names()` — a method that DOES NOT EXIST. The helper's
    own `except` caught the AttributeError and returned the empty set, so nothing
    would ever have been official and the feature would have shipped silently dead,
    with twelve green tests above it.

    That is this platform's second recurring defect — a double that stands in front
    of the code under test — and the guard against it is to drive the real object
    once.
    """

    async def test_the_helper_actually_reads_registered_adapters(self) -> None:
        from stackowl.channels.registry import ChannelRegistry
        from stackowl.tools import consent as mod

        class _Fake:
            channel_name = "telegram"

            async def send(self, chunks: object) -> None: ...
            async def send_text(self, text: str, **kw: object) -> None: ...
            async def receive(self) -> object: ...

        reg = ChannelRegistry.instance()
        reg.register(_Fake())  # type: ignore[arg-type]
        try:
            assert "telegram" in mod._gateway_channels(), (
                "the helper does not read the live registry — the API it calls is wrong"
            )
        finally:
            reg.reset()
