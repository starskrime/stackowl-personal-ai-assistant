"""REACT-3 / F031 — an owl-named provider that shadows an explicit manifest pin
WARNS so the collision is visible (the user's deliberate routing choice is not
silently overridden without a trace).

Precedence is unchanged (owl-named provider still wins — it is the most specific
per-owl binding), but when the owl's manifest ALSO carries an explicit, DIFFERENT
``provider_name`` pin, the override is logged at WARN naming both providers.
"""
from __future__ import annotations

import logging

import pytest

from stackowl.infra import recovery_context as rc
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.provider_select import select_tool_provider
from stackowl.pipeline.state import PipelineState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


class _FakeOwlRegistry:
    def __init__(self, manifest: OwlAgentManifest) -> None:
        self._manifest = manifest

    def get(self, owl_name: str) -> OwlAgentManifest:
        return self._manifest


class _FakeServices:
    def __init__(self, registry: ProviderRegistry, manifest: OwlAgentManifest) -> None:
        self.provider_registry = registry
        self.owl_registry = _FakeOwlRegistry(manifest)


def _state(owl_name: str = "scout") -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s1", input_text="hi", channel="cli",
        owl_name=owl_name, pipeline_step="execute",
    )


def test_owl_named_provider_warns_when_it_shadows_manifest_pin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reg = ProviderRegistry()
    # A provider registered under the OWL's own name ("scout") AND a different
    # provider the manifest explicitly pins.
    reg.register_mock("scout", MockProvider(name="scout"), tier="standard")
    reg.register_mock("pinned-claude", MockProvider(name="pinned-claude"), tier="powerful")
    manifest = OwlAgentManifest(
        name="scout", role="r", system_prompt="p",
        model_tier="powerful", provider_name="pinned-claude",
    )
    services = _FakeServices(reg, manifest)

    token = rc.bind()
    try:
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            provider = select_tool_provider(reg, services, _state())
    finally:
        rc.reset(token)

    # Precedence unchanged: the owl-named provider still wins.
    assert provider.name == "scout"
    # But the collision is now VISIBLE — a WARN naming the shadowed manifest pin.
    warned = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "pinned-claude" in r.getMessage() + str(getattr(r, "_fields", ""))
    ]
    assert warned, "expected a WARN when the owl-named provider shadows manifest.provider_name"


def test_no_warn_when_owl_name_provider_matches_pin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No collision when the manifest pin IS the owl-named provider (same target)."""
    reg = ProviderRegistry()
    reg.register_mock("scout", MockProvider(name="scout"), tier="standard")
    manifest = OwlAgentManifest(
        name="scout", role="r", system_prompt="p",
        model_tier="powerful", provider_name="scout",
    )
    services = _FakeServices(reg, manifest)

    token = rc.bind()
    try:
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            provider = select_tool_provider(reg, services, _state())
    finally:
        rc.reset(token)

    assert provider.name == "scout"
    shadow_warns = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "shadow" in r.getMessage().lower()
    ]
    assert not shadow_warns


def test_no_warn_when_no_manifest_pin(caplog: pytest.LogCaptureFixture) -> None:
    """An owl-named provider with NO manifest pin is the byte-identical happy path."""
    reg = ProviderRegistry()
    reg.register_mock("scout", MockProvider(name="scout"), tier="standard")
    manifest = OwlAgentManifest(
        name="scout", role="r", system_prompt="p",
        model_tier="powerful", provider_name=None,
    )
    services = _FakeServices(reg, manifest)

    token = rc.bind()
    try:
        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            provider = select_tool_provider(reg, services, _state())
    finally:
        rc.reset(token)

    assert provider.name == "scout"
    shadow_warns = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "shadow" in r.getMessage().lower()
    ]
    assert not shadow_warns
