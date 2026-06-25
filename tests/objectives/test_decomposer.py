"""ObjectiveDecomposer — intent → ordered sub-goals (1B).

Mirrors the SecretaryRouter test shape: a MockProvider at the standard tier
returns canned text; the decomposer parses it into an ordered sub-goal list.
Parsing is unit-testable without a provider; fail-safe falls back to a single
sub-goal that IS the whole objective (so a decomposition miss never strands it).
"""

from __future__ import annotations

import pytest

from stackowl.objectives.decomposer import (
    _MAX_SUBGOALS,
    ObjectiveDecomposer,
)
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _registry(mock: MockProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_mock(mock.name, mock, tier="standard")
    return registry


def _decomposer(canned: str) -> ObjectiveDecomposer:
    mock = MockProvider(name="mock-standard", canned_text=canned)
    return ObjectiveDecomposer(provider_registry=_registry(mock))


# --------------------------------------------------------------- parse-only


def test_parse_strips_numbering_and_bullets() -> None:
    raw = "1. Check the weather\n2) Summarize the forecast\n- Notify if rain\n* Log it"
    parsed = ObjectiveDecomposer._parse_subgoals(raw)
    assert parsed == [
        "Check the weather",
        "Summarize the forecast",
        "Notify if rain",
        "Log it",
    ]


def test_parse_drops_blank_lines() -> None:
    raw = "Step one\n\n   \nStep two\n"
    assert ObjectiveDecomposer._parse_subgoals(raw) == ["Step one", "Step two"]


def test_parse_caps_at_max() -> None:
    raw = "\n".join(f"step {i}" for i in range(_MAX_SUBGOALS + 10))
    assert len(ObjectiveDecomposer._parse_subgoals(raw)) == _MAX_SUBGOALS


def test_prompt_includes_intent_and_asks_for_steps() -> None:
    d = _decomposer("x")
    p = d._build_prompt("monitor the build and fix failures").lower()
    assert "monitor the build and fix failures" in p
    assert "step" in p  # asks for concrete steps


# ----------------------------------------------------------- provider-driven


@pytest.mark.asyncio
async def test_decompose_returns_parsed_subgoals() -> None:
    d = _decomposer("1. fetch the page\n2. diff against last\n3. report changes")
    subs = await d.decompose("watch the page and report changes")
    assert subs == ["fetch the page", "diff against last", "report changes"]


@pytest.mark.asyncio
async def test_decompose_empty_reply_falls_back_to_whole_objective() -> None:
    d = _decomposer("   ")
    subs = await d.decompose("do the thing")
    assert subs == ["do the thing"]  # single sub-goal = the whole intent


@pytest.mark.asyncio
async def test_decompose_provider_unavailable_falls_back_to_whole_objective() -> None:
    # No provider registered for the standard tier → the tier cascade raises;
    # the decomposer must degrade to the whole-objective single sub-goal, never
    # strand the objective.
    d = ObjectiveDecomposer(provider_registry=ProviderRegistry())
    subs = await d.decompose("resilient objective")
    assert subs == ["resilient objective"]
