"""Task 4 — model-aware lean charter wired into assemble.

Three cases:
  1. Small-window provider (context_chars=8000 → window 2000 ≤ 8192) → lean charter.
  2. Large-window provider (context_chars=320000 → window clamped to 16384) → full charter.
  3. Failing provider selection (empty registry) → fail-safe: full charter, no crash.

Driven via the same idiom as test_assemble_skills.py: set_services + StepServices,
a real OwlRegistry with one registered owl, and a ProviderRegistry with
register_mock so select_tool_provider finds a provider.

The fake providers carry _config with the desired context_chars so resolve_window
takes the config-override fast path (no HTTP probe needed).
_WINDOW_CACHE is cleared before each test to avoid cross-test pollution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import stackowl.providers.model_window as mw
from stackowl.owls.base_prompt import behavioral_charter
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.providers.registry import ModelRoute, ProviderRegistry

# ---------------------------------------------------------------------------
# Minimal fake provider — carries _config with context_chars
# ---------------------------------------------------------------------------

@dataclass
class _FakeConfig:
    context_chars: int | None
    base_url: str | None = None
    default_model: str = "test-model"


class _FakeProvider:
    """A no-op provider stub that only needs _config + a few attrs for select/resolve."""

    protocol = "openai"

    def __init__(self, context_chars: int | None) -> None:
        self._config = _FakeConfig(context_chars=context_chars)

    @property
    def name(self) -> str:
        return "fake"

    async def health_check(self) -> Any:  # pragma: no cover
        from stackowl.health.status import HealthStatus
        return HealthStatus(name="fake", status="ok", latency_ms=0)


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def _make_state(owl_name: str = "test_owl") -> PipelineState:
    return PipelineState(
        trace_id="t-model-aware",
        session_id="s-model-aware",
        input_text="hello",
        channel="cli",
        owl_name=owl_name,
        pipeline_step="assemble",
    )


def _make_owl_registry(owl_name: str = "test_owl") -> OwlRegistry:
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(
        name=owl_name,
        role="assistant",
        system_prompt="Test persona.",
        model_tier="fast",
    ))
    return reg


def _make_provider_registry(context_chars: int | None) -> ProviderRegistry:
    provider_registry = ProviderRegistry()
    fake = _FakeProvider(context_chars=context_chars)
    provider_registry.register_mock("fake", fake, tier="fast")  # type: ignore[arg-type]
    return provider_registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_small_window_still_uses_full_charter() -> None:
    """Owner decision 2026-07-22: the charter no longer shrinks for a small
    window — a small-window model needs the FULL instructions most, not a
    trimmed version. context_chars=8000 → window=2000 (small), but assemble
    still picks the full charter. model_window is still resolved/stamped
    (other consumers — delivery_gate's honest acknowledgement,
    progress_tracker's adaptive threshold — still need it)."""
    mw._WINDOW_CACHE.clear()

    owl_registry = _make_owl_registry()
    provider_registry = _make_provider_registry(context_chars=8000)
    token = set_services(StepServices(
        owl_registry=owl_registry,
        provider_registry=provider_registry,
    ))
    try:
        from stackowl.pipeline.steps import assemble
        result = await assemble.run(_make_state())
    finally:
        reset_services(token)

    assert result.model_window is not None, "model_window must still be stamped"
    assert result.model_window <= 8192, (
        f"Expected small window ≤ 8192, got {result.model_window}"
    )
    sp = result.system_prompt or ""
    full_text = behavioral_charter()
    assert full_text in sp, (
        f"Full charter not found in system_prompt (window={result.model_window}).\n"
        f"system_prompt[:300]={sp[:300]!r}"
    )


@pytest.mark.asyncio
async def test_large_window_uses_full_charter() -> None:
    """context_chars=320000 → window clamped to 16384 (> 8192) → full charter."""
    mw._WINDOW_CACHE.clear()

    owl_registry = _make_owl_registry()
    provider_registry = _make_provider_registry(context_chars=320_000)
    token = set_services(StepServices(
        owl_registry=owl_registry,
        provider_registry=provider_registry,
    ))
    try:
        from stackowl.pipeline.steps import assemble
        result = await assemble.run(_make_state())
    finally:
        reset_services(token)

    assert result.model_window is not None, "model_window must be stamped"
    assert result.model_window >= 16384, (
        f"Expected large window ≥ 16384, got {result.model_window}"
    )
    sp = result.system_prompt or ""
    full_text = behavioral_charter()
    assert full_text in sp, (
        f"Full charter not found in system_prompt (window={result.model_window}).\n"
        f"system_prompt[:300]={sp[:300]!r}"
    )


@pytest.mark.asyncio
async def test_provider_selection_failure_falls_back_to_full() -> None:
    """Empty ProviderRegistry → select_tool_provider raises → fail-safe: full charter, no crash."""
    mw._WINDOW_CACHE.clear()

    owl_registry = _make_owl_registry()
    # An empty registry: select_tool_provider will raise ProviderNotFoundError /
    # AllProvidersUnavailableError — the assemble fail-safe must catch it and
    # emit the FULL charter (lean=False) without crashing.
    empty_registry = ProviderRegistry()
    token = set_services(StepServices(
        owl_registry=owl_registry,
        provider_registry=empty_registry,
    ))
    try:
        from stackowl.pipeline.steps import assemble
        result = await assemble.run(_make_state())
    finally:
        reset_services(token)

    # No crash — a system_prompt was produced.
    sp = result.system_prompt or ""
    assert sp, "system_prompt must be non-empty even when provider selection fails"
    full_text = behavioral_charter()
    assert full_text in sp, (
        "Expected full charter (fail-safe) when provider selection raises.\n"
        f"system_prompt[:300]={sp[:300]!r}"
    )
    # model_window should be None on failure (fail-safe: don't stamp a bogus value)
    assert result.model_window is None, (
        f"model_window should be None on provider-selection failure, got {result.model_window}"
    )


@pytest.mark.asyncio
async def test_tier_resolved_model_used_in_window_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 10: the window probe must use the TIER-RESOLVED model, not ``default_model``.

    A single provider ('acme') exposes two DIFFERENTLY-NAMED models routed to two
    different tiers (mirrors the fixture shape proven in
    tests/providers/test_provider_registry_multi_tier_membership.py):
      - 'acme-fast-model' serves tier='fast'
      - 'acme-powerful-model' serves tier='powerful'
    The provider's own ``_config.default_model`` is set to a THIRD, distinct string
    ('acme-unused-default') that matches neither routed model — so if the window
    probe fell back to ``default_model`` instead of the tier-resolved model, the
    captured value below would equal 'acme-unused-default', not 'acme-powerful-model'.

    The test owl has no owl-named/manifest provider pin, only ``model_tier='powerful'``,
    so ``select_tool_provider_plan`` must resolve via tier walking and hand back
    ``ToolProviderChoice(model='acme-powerful-model', ...)``.

    We monkeypatch ``resolve_window`` (via the ``stackowl.providers.model_window``
    module that assemble.py locally imports from at call time) to capture the exact
    ``model=`` keyword argument it receives — the most direct proof of what
    assemble.py actually threaded through. Pre-fix, assemble.py always passed
    ``_pc.default_model`` ('acme-unused-default'); post-fix it passes
    ``_choice.model`` ('acme-powerful-model'). This makes the assertion fail on the
    pre-fix code and pass on the current code (verified via git stash of the
    assemble.py fix with this test in place).
    """
    mw._WINDOW_CACHE.clear()

    captured: dict[str, Any] = {}

    async def _spy_resolve_window(
        *,
        provider_name: str,
        base_url: str | None,
        model: str,
        context_chars: int | None,
        protocol: str,
        api_key: str | None = None,
    ) -> int:
        captured["model"] = model
        return 16384

    monkeypatch.setattr(mw, "resolve_window", _spy_resolve_window)

    # ONE provider, TWO differently-named models routed to two different tiers.
    provider_registry = ProviderRegistry()
    acme_provider = _FakeProvider(context_chars=320_000)
    acme_provider._config.default_model = "acme-unused-default"
    provider_registry.register_mock(
        "acme", acme_provider,
        models=(
            ModelRoute(model="acme-fast-model", tiers=("fast",)),
            ModelRoute(model="acme-powerful-model", tiers=("powerful",)),
        ),
    )

    # Owl with model_tier='powerful' and no provider pin — tier resolution must
    # land on the 'powerful' ModelRoute, i.e. 'acme-powerful-model'.
    owl_registry = OwlRegistry()
    owl_registry.register(OwlAgentManifest(
        name="powerful_owl",
        role="assistant",
        system_prompt="Test persona.",
        model_tier="powerful",
    ))

    token = set_services(StepServices(
        owl_registry=owl_registry,
        provider_registry=provider_registry,
    ))
    try:
        from stackowl.pipeline.steps import assemble
        await assemble.run(_make_state(owl_name="powerful_owl"))
    finally:
        reset_services(token)

    assert "model" in captured, "resolve_window was never called"
    assert captured["model"] == "acme-powerful-model", (
        f"Expected the window probe to use the tier-resolved model "
        f"'acme-powerful-model', but resolve_window was called with "
        f"model={captured['model']!r}. This means assemble.py fell back to "
        f"the provider's default_model instead of threading the tier-resolved "
        f"model from select_tool_provider_plan's ToolProviderChoice."
    )
    assert captured["model"] != "acme-unused-default"
