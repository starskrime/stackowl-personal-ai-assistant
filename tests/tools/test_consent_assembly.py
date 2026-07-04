"""OPS-5 (F149) — ConsentAssembly seam test.

The consent wiring was extracted from the _phase_gateway monolith into a
unit-testable assembly. This asserts the seam builds a routing prompter (with
the CLI prompter pre-registered) + a consent gate over the audit logger, without
booting the gateway, and that channel prompters can register afterwards (the
mutable-routing contract the Telegram/Slack/Discord adapters rely on).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from stackowl.tools.consent import RoutingPrompter, TrustTier
from stackowl.tools.consent_assembly import ConsentAssembly, ConsentComponents
from stackowl.tools.registry import ConsequentialActionGate


def test_build_returns_gate_and_routing_prompter() -> None:
    components = ConsentAssembly.build(MagicMock())
    assert isinstance(components, ConsentComponents)
    assert isinstance(components.routing_prompter, RoutingPrompter)
    assert isinstance(components.consent_gate, ConsequentialActionGate)


def test_cli_prompter_registered_and_others_can_register_later() -> None:
    components = ConsentAssembly.build(MagicMock())
    routing = components.routing_prompter
    # CLI is registered at build time.
    assert "cli" in routing._by_channel  # noqa: SLF001 — seam assertion
    # A channel prompter can be added afterwards (mutable-routing contract).
    later = MagicMock()
    routing.register("telegram", later)
    assert "telegram" in routing._by_channel  # noqa: SLF001


def test_scheduled_skill_synthesizer_identity_is_auto_trusted() -> None:
    """Task 4 Finding 2 (user decision): the daily SkillSynthesizer job is
    unattended (no human ever present to approve a prompt), so its DEDICATED
    scheduled identity is seeded with TrustTier.AUTO here — while the LIVE
    identity (used when a human IS present) is deliberately absent, so it
    stays on normal ALWAYS_ASK consent."""
    from stackowl.skills.synthesizer import (
        _CONSENT_TOOL_NAME_LIVE,
        _CONSENT_TOOL_NAME_SCHEDULED,
    )

    components = ConsentAssembly.build(MagicMock())
    tiers = components.consent_gate.policy.tiers
    assert tiers.get(_CONSENT_TOOL_NAME_SCHEDULED) is TrustTier.AUTO
    assert _CONSENT_TOOL_NAME_LIVE not in tiers
