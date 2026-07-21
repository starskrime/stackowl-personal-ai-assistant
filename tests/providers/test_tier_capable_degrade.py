"""PROV-4 (F125) — a missing `powerful` provider cascades up (most-capable
available), surfaced as tier-degraded — never a silent arbitrary weak substitute.
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import ProviderNotFoundError
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def test_capable_degrade_prefers_most_capable_available_not_config_order() -> None:
    reg = ProviderRegistry()
    # Config order deliberately puts the WEAK one first so a config-order fallback
    # would pick it. A capability cascade must prefer 'standard' over 'fast'.
    reg.register_mock("weak", MockProvider(), tier="fast")
    reg.register_mock("mid", MockProvider(), tier="standard")

    provider, _model, degraded_from = reg.resolve_capable_or_degrade("powerful")
    assert provider is reg.get("mid"), "must pick the most-capable available tier"
    assert degraded_from == "powerful"


def test_capable_degrade_exact_match_not_degraded() -> None:
    reg = ProviderRegistry()
    reg.register_mock("big", MockProvider(), tier="powerful")
    provider, _model, degraded_from = reg.resolve_capable_or_degrade("powerful")
    assert provider is reg.get("big")
    assert degraded_from is None


def test_capable_degrade_only_local_weak_is_still_surfaced() -> None:
    reg = ProviderRegistry()
    reg.register_mock("ollama", MockProvider(), tier="local", is_local=True)
    provider, _model, degraded_from = reg.resolve_capable_or_degrade("powerful")
    assert provider is reg.get("ollama")
    # The only option is a weak local model — caller MUST be told it's degraded.
    assert degraded_from == "powerful"


def test_capable_degrade_raises_when_empty() -> None:
    reg = ProviderRegistry()
    with pytest.raises(ProviderNotFoundError):
        reg.resolve_capable_or_degrade("powerful")
