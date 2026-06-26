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


# --------------------------------------------------- acceptance markers (B3)


@pytest.mark.asyncio
async def test_decompose_specs_parses_produces_file_marker() -> None:
    """A step the model marks with <<produces-file>> carries an artifact
    acceptance criterion; the marker is stripped from the description. Steps
    without the marker carry no criterion (legacy no-error path)."""
    d = _decomposer(
        "fetch the video page\n"
        "download the video to a file <<produces-file: downloads>>\n"
        "notify the owner"
    )
    specs = await d.decompose_specs("download the video")
    assert [s.description for s in specs] == [
        "fetch the video page",
        "download the video to a file",
        "notify the owner",
    ]
    assert specs[0].acceptance_criteria is None
    assert specs[2].acceptance_criteria is None
    crit = specs[1].acceptance_criteria
    assert crit is not None
    assert crit.kind == "artifact"
    assert crit.artifact_dir == "downloads"


@pytest.mark.asyncio
async def test_decompose_specs_marker_without_dir_defaults_to_workspace() -> None:
    """The bare marker (no directory) declares an artifact with no specific dir
    (artifact_dir=None ⇒ the workspace root is observed)."""
    d = _decomposer("save the report <<produces-file>>")
    specs = await d.decompose_specs("save the report")
    assert specs[0].description == "save the report"
    crit = specs[0].acceptance_criteria
    assert crit is not None and crit.kind == "artifact" and crit.artifact_dir is None


@pytest.mark.asyncio
async def test_decompose_legacy_returns_plain_descriptions() -> None:
    """decompose() keeps its list[str] contract — descriptions only, markers
    stripped — so every existing caller is byte-identical."""
    d = _decomposer("save the report <<produces-file>>\nnotify")
    assert await d.decompose("x") == ["save the report", "notify"]


def test_prompt_documents_the_artifact_marker() -> None:
    """The decomposition prompt must teach the model the marker convention so it
    can declare artifact-producing steps (general instruction, not a keyword list)."""
    d = _decomposer("x")
    p = d._build_prompt("download something")
    assert "<<produces-file>>" in p
