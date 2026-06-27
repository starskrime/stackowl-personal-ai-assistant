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
