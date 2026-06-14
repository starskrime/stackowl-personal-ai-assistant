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
from stackowl.owls.base_prompt import behavioral_charter, behavioral_charter_lean
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.providers.registry import ProviderRegistry

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
async def test_small_window_uses_lean_charter() -> None:
    """context_chars=8000 → window=2000 (≤ 8192) → assemble picks the lean charter."""
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

    assert result.model_window is not None, "model_window must be stamped"
    assert result.model_window <= 8192, (
        f"Expected small window ≤ 8192, got {result.model_window}"
    )
    sp = result.system_prompt or ""
    lean_text = behavioral_charter_lean()
    assert lean_text in sp, (
        f"Lean charter not found in system_prompt (window={result.model_window}).\n"
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
