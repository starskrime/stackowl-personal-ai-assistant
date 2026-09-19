"""Story 3.4 (AC5) — the provenance auto-grant never applies to `web` or `voice`,
even if a future adapter (or a test double) registers one of those names into
`ChannelRegistry`'s live set.

`_is_official_channel` is the ONE helper both `AutonomousPrompter.prompt()` and
`ConsentPolicy.request()` now call for "is this an official origin" (Story 3.4
Code Map, replacing the duplicated inline expression). Neither channel has a
real adapter today (`_NEVER_OFFICIAL_CHANNELS`'s own docstring: "a forward
guard, not a fix for an observed leak") — this file pins that a live-registry
entry can never override the guard.
"""

from __future__ import annotations

import pytest

from stackowl.tools.consent import _is_official_channel


class TestNeverOfficialEvenWhenLive:
    def test_web_is_never_official(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import stackowl.tools.consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"web", "telegram"}))

        assert _is_official_channel("web") is False

    def test_voice_is_never_official(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import stackowl.tools.consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"voice", "telegram"}))

        assert _is_official_channel("voice") is False

    def test_a_real_adapter_channel_is_still_official(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The guard is narrow — it must not swallow every channel, only the two
        named ones."""
        import stackowl.tools.consent as mod

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"telegram"}))

        assert _is_official_channel("telegram") is True

    def test_empty_or_none_channel_is_never_official(self) -> None:
        assert _is_official_channel("") is False


class TestTheGuardHoldsThroughBothRealCallSites:
    """The unit-level guard above is necessary but not sufficient — drive it
    through both real call sites that used to duplicate the inline
    expression, so a future edit to either site cannot silently reintroduce
    a second, divergent copy of the rule."""

    async def test_autonomous_prompter_never_grants_authority_widening_on_web(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import stackowl.tools.consent as mod
        from stackowl.tools.consent import AutonomousPrompter, ConsentRequest, ConsentScope

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"web"}))

        scope = await AutonomousPrompter().prompt(ConsentRequest(
            tool_name="owl_build", channel="web", session_key="s1",
            category="authority_widening", allow_relaxation=False,
        ))

        assert scope is ConsentScope.DENY

    async def test_consent_policy_never_grants_authority_widening_on_voice(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import stackowl.tools.consent as mod
        from stackowl.tools.consent import ConsentPolicy, FailClosedPrompter

        monkeypatch.setattr(mod, "_gateway_channels", lambda: frozenset({"voice"}))

        allowed = await ConsentPolicy(prompter=FailClosedPrompter()).request(
            tool_name="owl_build", channel="voice", session_key="s1",
            category="authority_widening", summary="",
        )

        assert allowed is False
