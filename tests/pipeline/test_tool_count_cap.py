"""Configurable tool-count cap (Phase 0): resolve_tool_count_cap + fit_items."""

from __future__ import annotations

from stackowl.pipeline.context_budget import (
    HARD_TOOL_COUNT_CAP,
    fit_items,
    resolve_tool_count_cap,
)


def test_configured_value_is_used() -> None:
    assert resolve_tool_count_cap(12) == 12
    assert resolve_tool_count_cap(18) == 18
    assert resolve_tool_count_cap(1) == 1


def test_none_falls_back_to_default() -> None:
    assert resolve_tool_count_cap(None) == HARD_TOOL_COUNT_CAP


def test_non_positive_falls_back_to_default() -> None:
    assert resolve_tool_count_cap(0) == HARD_TOOL_COUNT_CAP
    assert resolve_tool_count_cap(-5) == HARD_TOOL_COUNT_CAP


def test_fit_items_honors_low_cap_and_never_drops_guaranteed() -> None:
    # Generous token budget so ONLY the count cap binds; guaranteed always kept.
    out = fit_items(
        guaranteed=["g1", "g2"],
        candidates=["c1", "c2", "c3", "c4", "c5"],
        budget=10_000,
        size_of=lambda _x: 1,
        hard_cap=resolve_tool_count_cap(4),
    )
    assert out == ["g1", "g2", "c1", "c2"]  # 2 guaranteed + 2 candidates == cap 4
