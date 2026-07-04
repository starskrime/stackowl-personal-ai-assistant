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


# ----------------------------------------------- complexity markers (Task 3)


def test_prompt_documents_the_complexity_marker() -> None:
    """The decomposition prompt must teach the model to estimate per-step
    complexity so adaptive decomposition has a signal to act on."""
    d = _decomposer("x")
    p = d._build_prompt("do something").lower()
    assert "<<complexity" in p


@pytest.mark.asyncio
async def test_decompose_specs_parses_complexity_marker() -> None:
    """A step's <<complexity: N>> marker becomes estimated_complexity and is
    stripped from the description; a step without the marker defaults to 0.0
    (no signal — conservative, never triggers recursion on its own)."""
    d = _decomposer(
        "fetch the page <<complexity: 0.1>>\n"
        "rebuild and redeploy the whole service <<complexity: 0.9>>\n"
        "notify the owner"
    )
    specs = await d.decompose_specs("do a big thing")
    assert [s.description for s in specs] == [
        "fetch the page",
        "rebuild and redeploy the whole service",
        "notify the owner",
    ]
    assert specs[0].estimated_complexity == pytest.approx(0.1)
    assert specs[1].estimated_complexity == pytest.approx(0.9)
    assert specs[2].estimated_complexity == 0.0


@pytest.mark.asyncio
async def test_decompose_specs_complexity_marker_clamped_to_unit_range() -> None:
    """An out-of-range or garbled value is clamped/defaulted rather than
    propagated raw — the threshold comparison downstream must stay meaningful."""
    d = _decomposer("do a huge amount of work <<complexity: 5>>")
    specs = await d.decompose_specs("x")
    assert specs[0].estimated_complexity == 1.0


@pytest.mark.asyncio
async def test_decompose_specs_combines_produces_file_and_complexity_markers() -> None:
    """Both markers may appear on the same line, in either order, and both are
    parsed and stripped correctly."""
    d = _decomposer("download the archive <<produces-file: downloads>> <<complexity: 0.8>>")
    specs = await d.decompose_specs("x")
    assert specs[0].description == "download the archive"
    assert specs[0].estimated_complexity == pytest.approx(0.8)
    assert specs[0].acceptance_criteria is not None
    assert specs[0].acceptance_criteria.artifact_dir == "downloads"


@pytest.mark.asyncio
async def test_decompose_fallback_specs_have_zero_complexity() -> None:
    """The fail-safe single-spec fallback (empty/garbled reply, or provider
    unavailable) never carries a complexity signal — it must never trigger
    recursive decomposition of the whole objective."""
    d = _decomposer("   ")
    specs = await d.decompose_specs("do the thing")
    assert len(specs) == 1
    assert specs[0].estimated_complexity == 0.0
