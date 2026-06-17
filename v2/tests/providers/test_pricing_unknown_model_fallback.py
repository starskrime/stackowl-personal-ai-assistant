"""PROV-2 (F128) — an unknown CLOUD model must not be billed as $0.

A model absent from the pricing table that is NOT served by a local backend
gets a conservative configurable fallback price (logged at WARNING). Only a
known-local backend legitimately stays $0.
"""

from __future__ import annotations

import logging

from stackowl.providers.pricing.loader import PricingLoader


def test_unknown_cloud_model_uses_conservative_fallback_not_zero(caplog) -> None:
    loader = PricingLoader(unknown_cloud_per_1m_usd=20.0)
    with caplog.at_level(logging.WARNING):
        cost = loader.estimate("some-new-cloud-model", 1_000_000, 1_000_000, is_local=False)
    # 1M input + 1M output at 20/1M each = 40.0 USD — emphatically not 0.
    assert cost > 0.0
    assert abs(cost - 40.0) < 1e-9
    assert any("fallback" in r.message.lower() or "unknown" in r.message.lower() for r in caplog.records)


def test_unknown_local_model_stays_zero() -> None:
    loader = PricingLoader(unknown_cloud_per_1m_usd=20.0)
    cost = loader.estimate("gemma-3-12b", 1_000_000, 1_000_000, is_local=True)
    assert cost == 0.0


def test_known_model_pricing_unchanged_regardless_of_locality() -> None:
    loader = PricingLoader(unknown_cloud_per_1m_usd=20.0)
    # gpt-4o is priced at 5.0 in / 15.0 out per 1M — locality must not alter it.
    cloud = loader.estimate("gpt-4o", 1_000_000, 1_000_000, is_local=False)
    local = loader.estimate("gpt-4o", 1_000_000, 1_000_000, is_local=True)
    assert cloud == local
    assert abs(cloud - 20.0) < 1e-9
