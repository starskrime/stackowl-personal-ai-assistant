"""Model-routing fix — tests for tier/provider precedence + loud degrade.

Covers:
1. get_by_tier with a missing tier still returns the fallback AND logs a loud,
   actionable degrade warning.
2. _select_tool_provider precedence rules:
   - manifest.provider_name wins when the provider is registered
   - a pin beats a session tier
   - falls back to tier routing when provider_name is unregistered (warns)
   - session tier overrides manifest tier
   - manifest tier used when there is no session pref
   - defaults to 'powerful' when neither is set
   - a desired tier with no provider still resolves (degraded) and warns
   - the final selection is logged at INFO
"""

from __future__ import annotations

import logging
from typing import Literal

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(*pairs: tuple[str, str]) -> ProviderRegistry:
    """Build a ProviderRegistry with (name, tier) pairs of MockProvider."""
    reg = ProviderRegistry()
    for name, tier in pairs:
        reg.register_mock(name, MockProvider(name=name), tier=tier)
    return reg


def _make_manifest(
    *,
    model_tier: Literal["fast", "standard", "powerful", "local"] = "powerful",
    provider_name: str | None = None,
) -> OwlAgentManifest:
    return OwlAgentManifest(
        name="testowl",
        role="test",
        system_prompt="test system prompt",
        model_tier=model_tier,
        provider_name=provider_name,
    )


def _make_state(*, session_id: str = "sess-test", owl_name: str = "testowl") -> PipelineState:
    return PipelineState(
        trace_id="trace-test",
        session_id=session_id,
        input_text="hi",
        channel="cli",
        owl_name=owl_name,
        pipeline_step="execute",
    )


class _FakeOwlRegistry:
    """Returns a manifest, or raises (unknown owl) when manifest is None."""

    def __init__(self, manifest: OwlAgentManifest | None) -> None:
        self._manifest = manifest

    def get(self, owl_name: str) -> OwlAgentManifest:
        if self._manifest is None:
            from stackowl.exceptions import OwlNotFoundError

            raise OwlNotFoundError(owl_name)
        return self._manifest


class _FakeServices:
    def __init__(self, registry: ProviderRegistry, manifest: OwlAgentManifest | None) -> None:
        self.provider_registry = registry
        self.owl_registry = _FakeOwlRegistry(manifest)


@pytest.fixture(autouse=True)
def _clear_session_tiers() -> None:
    """Keep the per-session tier cache clean between tests."""
    from stackowl.commands.tier_command import reset_session_tiers

    reset_session_tiers()


def _set_session_tier(session_id: str, tier: str) -> None:
    import stackowl.commands.tier_command as tc

    tc._fallback_prefs[session_id] = tier


# ---------------------------------------------------------------------------
# 1. get_by_tier: missing tier still returns fallback, warns loudly
# ---------------------------------------------------------------------------


class TestGetByTierWarningOnFallback:
    def test_returns_fallback_when_tier_missing(self) -> None:
        reg = _make_registry(("only-provider", "fast"))
        provider = reg.get_by_tier("standard")  # "standard" unregistered
        assert provider is reg.get("only-provider")  # fallback returned unchanged

    def test_logs_warning_on_tier_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        reg = _make_registry(("only-provider", "fast"))
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            reg.get_by_tier("standard")
        messages = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "degraded" in messages.lower()

    def test_no_warning_when_tier_exists(self, caplog: pytest.LogCaptureFixture) -> None:
        reg = _make_registry(("fast-p", "fast"), ("standard-p", "standard"))
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            reg.get_by_tier("fast")
        degraded = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "degraded" in r.message.lower()
        ]
        assert len(degraded) == 0


# ---------------------------------------------------------------------------
# 2. _select_tool_provider precedence
# ---------------------------------------------------------------------------


class TestSelectToolProvider:
    """Precedence: provider_name pin > session tier > manifest tier > 'powerful'."""

    def _select(self):
        from stackowl.pipeline.provider_select import select_tool_provider

        return select_tool_provider

    def test_owl_named_provider_wins_over_everything(self) -> None:
        """A provider registered under the owl's own name is the top precedence."""
        reg = _make_registry(("secretary", "standard"), ("powerful-p", "powerful"))
        # Even with a manifest pin AND a session tier set, the owl-named provider wins.
        manifest = _make_manifest(model_tier="powerful", provider_name="powerful-p")
        services = _FakeServices(reg, manifest)
        _set_session_tier("sess-x", "powerful")
        provider = self._select()(reg, services, _make_state(owl_name="secretary", session_id="sess-x"))
        assert provider is reg.get("secretary")

    def test_uses_pinned_provider_name_when_registered(self) -> None:
        reg = _make_registry(("pinned-fast", "fast"), ("fallback-powerful", "powerful"))
        manifest = _make_manifest(model_tier="powerful", provider_name="pinned-fast")
        services = _FakeServices(reg, manifest)
        provider = self._select()(reg, services, _make_state(session_id="no-tier"))
        assert provider is reg.get("pinned-fast")

    def test_pin_beats_session_tier(self) -> None:
        """An explicit provider_name pin wins even over a session tier preference."""
        reg = _make_registry(("pinned-standard", "standard"), ("fast-p", "fast"))
        manifest = _make_manifest(model_tier="powerful", provider_name="pinned-standard")
        services = _FakeServices(reg, manifest)
        _set_session_tier("sess-fast", "fast")
        provider = self._select()(reg, services, _make_state(session_id="sess-fast"))
        assert provider is reg.get("pinned-standard")

    def test_falls_back_to_tier_when_provider_name_not_registered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reg = _make_registry(("powerful-p", "powerful"))
        manifest = _make_manifest(model_tier="powerful", provider_name="nonexistent-provider")
        services = _FakeServices(reg, manifest)
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            provider = self._select()(reg, services, _make_state(session_id="no-tier"))
        assert provider is reg.get("powerful-p")
        warnings = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "not registered" in warnings.lower() or "manifest" in warnings.lower()

    def test_session_tier_overrides_manifest_tier(self) -> None:
        reg = _make_registry(("fast-p", "fast"), ("powerful-p", "powerful"))
        manifest = _make_manifest(model_tier="powerful", provider_name=None)
        services = _FakeServices(reg, manifest)
        _set_session_tier("sess-fast", "fast")
        provider = self._select()(reg, services, _make_state(session_id="sess-fast"))
        assert provider is reg.get("fast-p")

    def test_manifest_tier_used_when_no_session_pref(self) -> None:
        reg = _make_registry(("fast-p", "fast"), ("standard-p", "standard"))
        manifest = _make_manifest(model_tier="standard", provider_name=None)
        services = _FakeServices(reg, manifest)
        provider = self._select()(reg, services, _make_state(session_id="no-tier"))
        assert provider is reg.get("standard-p")

    def test_defaults_to_powerful_when_no_manifest_no_session(self) -> None:
        reg = _make_registry(("powerful-p", "powerful"))
        services = _FakeServices(reg, None)  # unknown owl → no manifest
        provider = self._select()(reg, services, _make_state(owl_name="unknown", session_id="no-tier"))
        assert provider is reg.get("powerful-p")

    def test_degrades_and_warns_when_desired_tier_absent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Desired tier has no provider → get_by_tier degrades + warns, still returns one."""
        reg = _make_registry(("fast-p", "fast"))  # no "powerful" provider
        manifest = _make_manifest(model_tier="powerful", provider_name=None)
        services = _FakeServices(reg, manifest)
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            provider = self._select()(reg, services, _make_state(session_id="no-tier"))
        assert provider is reg.get("fast-p")
        warnings = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "degraded" in warnings.lower()

    def test_logs_info_on_selected_provider(self, caplog: pytest.LogCaptureFixture) -> None:
        reg = _make_registry(("powerful-p", "powerful"))
        manifest = _make_manifest(model_tier="powerful", provider_name=None)
        services = _FakeServices(reg, manifest)
        with caplog.at_level(logging.INFO, logger="stackowl.engine"):
            self._select()(reg, services, _make_state(session_id="no-tier"))
        info = " ".join(r.message for r in caplog.records if r.levelno >= logging.INFO)
        assert "tool provider selected" in info.lower()
