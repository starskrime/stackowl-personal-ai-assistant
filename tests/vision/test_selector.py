"""E10-S1 — VisionSelector picks a vision provider LOCAL-FIRST; unavailable when none."""

from __future__ import annotations

import pytest

from stackowl.providers.circuit_breaker import CircuitState
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.vision.selector import VisionSelector


async def _force_open(reg: ProviderRegistry, name: str) -> None:
    """Drive a provider's breaker OPEN by failing through call() to threshold."""
    breaker = reg.get_circuit_breaker(name)
    assert breaker is not None

    async def _boom() -> None:
        raise RuntimeError("boom")

    # Default threshold is 3; drive enough failures to open, then stop (once OPEN,
    # call() rejects without awaiting, which would leak an un-awaited coroutine).
    for _ in range(3):
        if breaker.state is CircuitState.OPEN:
            break
        with pytest.raises(RuntimeError):
            await breaker.call(_boom())
    assert breaker.state is CircuitState.OPEN


class _VisionMock(MockProvider):
    """A MockProvider that reports a chosen vision capability."""

    def __init__(self, name: str, *, vision: bool) -> None:
        super().__init__(name=name)
        self._vision = vision

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        return self._vision


def _registry() -> ProviderRegistry:
    return ProviderRegistry()


def test_local_preferred_over_cloud() -> None:
    reg = _registry()
    # Cloud provider: a real cloud base_url → NOT local.
    reg.register_mock(
        "cloud", _VisionMock("cloud", vision=True), tier="powerful",
        base_url="https://api.anthropic.com/v1",
    )
    # Local provider mirrors the SHIPPED ollama.yaml shape: tier ``fast`` (NOT
    # ``local``) + a localhost base_url. Locality is derived from the URL.
    reg.register_mock(
        "local", _VisionMock("local", vision=True), tier="fast",
        base_url="http://localhost:11434/v1",
    )

    sel = VisionSelector(reg).select()

    assert sel.available
    assert sel.provider is not None
    assert sel.provider.name == "local"
    assert sel.is_local is True


def test_default_ollama_config_shape_classifies_local() -> None:
    """The DEFAULT shipped ollama config (tier: fast + localhost base_url) MUST be
    classified LOCAL and preferred over a real cloud provider — the exact failure
    the prior tier=='local' check missed (tier and locality are orthogonal)."""
    reg = _registry()
    reg.register_mock(
        "openai", _VisionMock("openai", vision=True), tier="powerful",
        base_url="https://api.openai.com/v1",
    )
    # Exactly the shipped setup/providers/ollama.yaml: tier fast, localhost base_url.
    reg.register_mock(
        "ollama", _VisionMock("ollama", vision=True), tier="fast",
        base_url="http://localhost:11434/v1",
    )

    sel = VisionSelector(reg).select()

    assert sel.available
    assert sel.provider is not None
    assert sel.provider.name == "ollama"  # local-first fired for the default config
    assert sel.is_local is True
    # And the registry classifies it local directly, despite tier == "fast".
    assert reg.is_local(reg.get("ollama")) is True
    assert reg.tiers_of(reg.get("ollama")) == ("fast",)


def test_private_ip_base_url_classifies_local() -> None:
    """A private-network host (e.g. a LAN Ollama at 192.168.x) is also local."""
    reg = _registry()
    reg.register_mock(
        "lan", _VisionMock("lan", vision=True), tier="fast",
        base_url="http://192.168.1.50:11434/v1",
    )

    sel = VisionSelector(reg).select()

    assert sel.is_local is True
    assert sel.provider is not None
    assert sel.provider.name == "lan"


def test_cloud_providers_classify_not_local() -> None:
    """Real cloud base_urls (anthropic/openai/gemini) classify as NOT local."""
    reg = _registry()
    reg.register_mock(
        "anthropic", _VisionMock("anthropic", vision=True), tier="powerful",
        base_url="https://api.anthropic.com/v1",
    )
    reg.register_mock(
        "openai", _VisionMock("openai", vision=True), tier="standard",
        base_url="https://api.openai.com/v1",
    )
    reg.register_mock(
        "gemini", _VisionMock("gemini", vision=True), tier="fast",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )

    for name in ("anthropic", "openai", "gemini"):
        assert reg.is_local(reg.get(name)) is False

    sel = VisionSelector(reg).select()
    assert sel.available
    assert sel.is_local is False  # egress signal for S2: cloud = image leaves the box


def test_cloud_chosen_when_no_local() -> None:
    reg = _registry()
    reg.register_mock(
        "cloud", _VisionMock("cloud", vision=True), tier="powerful",
        base_url="https://api.anthropic.com/v1",
    )

    sel = VisionSelector(reg).select()

    assert sel.available
    assert sel.provider is not None
    assert sel.provider.name == "cloud"
    assert sel.is_local is False  # egress signal for S2: cloud = image leaves the box


def test_unavailable_when_no_vision_provider() -> None:
    reg = _registry()
    reg.register_mock("text_only", _VisionMock("text_only", vision=False), tier="local")

    sel = VisionSelector(reg).select()

    assert not sel.available
    assert sel.provider is None
    assert sel.reason is not None
    assert "vision" in sel.reason.lower()


def test_unavailable_when_registry_empty() -> None:
    sel = VisionSelector(_registry()).select()
    assert not sel.available
    assert sel.reason is not None


@pytest.mark.asyncio
async def test_open_circuit_provider_skipped() -> None:
    reg = _registry()
    reg.register_mock(
        "local", _VisionMock("local", vision=True), tier="fast",
        base_url="http://localhost:11434/v1",
    )
    reg.register_mock(
        "cloud", _VisionMock("cloud", vision=True), tier="powerful",
        base_url="https://api.anthropic.com/v1",
    )
    # Trip the local provider's breaker → selection must fall back to cloud.
    await _force_open(reg, "local")

    sel = VisionSelector(reg).select()

    assert sel.available
    assert sel.provider is not None
    assert sel.provider.name == "cloud"
    assert sel.is_local is False
