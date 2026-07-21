"""F-14 — the LLM-derived acceptance layer is flag-OFF by default, and that skip
must be OBSERVABLE, not a silent early-return. "We did not verify an undeclared
claim" should be visible in the trace.

The default stays OFF (no latency/cost per turn). These tests pin the observability
seam: ``acceptance_skip_reason`` names WHY the layer is skipped (so a caller can log
it), and the deriver itself emits that reason instead of returning None silently.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.pipeline.acceptance_llm import (
    LlmAcceptanceDeriver,
    acceptance_skip_reason,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ModelRoute, ProviderRegistry


def test_skip_reason_present_when_tier_unset() -> None:
    reason = acceptance_skip_reason("")
    assert reason is not None
    assert "acceptance_tier" in reason


def test_skip_reason_absent_when_tier_set() -> None:
    assert acceptance_skip_reason("standard") is None


@pytest.mark.asyncio
async def test_derive_skips_observably_when_tier_unset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    deriver = LlmAcceptanceDeriver(provider_registry=object(), tier="")  # type: ignore[arg-type]
    with caplog.at_level(logging.DEBUG):
        result = await deriver.derive(intent="save it", draft="All done, saved to disk")
    assert result is None
    # The skip is no longer silent — a skip record is emitted and it carries the
    # config-driven reason naming the unset tier.
    skip_records = [r for r in caplog.records if "skipped" in r.getMessage()]
    assert skip_records, "expected an observable skip log when the tier is unset"
    reasons = [getattr(r, "_fields", {}).get("reason", "") for r in skip_records]
    assert any("acceptance_tier" in reason for reason in reasons)


# ---------------------------------------------------------------------------
# Task 14 — derive() threads the RESOLVED tier model into provider.complete(),
# instead of hardcoding model="".
# ---------------------------------------------------------------------------


class _ModelCapturingProvider(ModelProvider):
    """Records the ``model`` kwarg its ``complete()`` was called with."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.seen_model: str | None = None

    @property
    def name(self) -> str:
        return "model-capturing-acceptance"

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.seen_model = model
        return CompletionResult(
            content=self._reply, input_tokens=1, output_tokens=1,
            model="model-capturing-acceptance", provider_name=self.name, duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield self._reply


@pytest.mark.asyncio
async def test_derive_threads_resolved_tier_model_to_complete() -> None:
    """``derive()`` resolves the configured tier's provider+model via
    ``get_with_cascade(self._tier)`` and forwards the RESOLVED model
    into ``provider.complete()``.

    Genuinely discriminating: if ``derive()`` still called the old
    ``get_with_cascade`` (dropping the model) or hardcoded ``model=""``,
    ``seen_model`` would stay "" instead of the sentinel resolved model.
    """
    provider = _ModelCapturingProvider("ARTIFACT: /tmp/out")
    registry = ProviderRegistry()
    registry.register_mock(
        "mock-standard", provider,
        models=(ModelRoute(model="acceptance-standard-resolved", tiers=("standard",)),),
    )
    deriver = LlmAcceptanceDeriver(provider_registry=registry, tier="standard")

    result = await deriver.derive(intent="save it", draft="All done, saved to disk")

    assert result is not None
    assert provider.seen_model == "acceptance-standard-resolved", (
        f"expected the resolved tier model to reach complete(); got {provider.seen_model!r}"
    )
