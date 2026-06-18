"""Unit tests for owl_build pure guardrails (structural name-quality, soft-cap,
consent-summary). Language-neutral — NO English wordlists."""
from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.meta.owl_build_guards import (
    MAX_AGENT_OWLS,
    consent_summary,
    count_agent_owls,
    name_quality_error,
)


def _reg(*names: str) -> OwlRegistry:
    reg = OwlRegistry()
    for n in names:
        reg.register(
            OwlAgentManifest(
                name=n,
                role=n,
                system_prompt="p",
                model_tier="fast",
                origin="agent",
                created_by="secretary",
                creation_ceiling=BoundsSpec(tools=frozenset()),
                bounds=BoundsSpec(tools=frozenset()),
            ),
            source_name="t",
        )
    return reg


def test_name_quality_rejects_trailing_digit_duplicate() -> None:
    reg = _reg("researcher")
    assert name_quality_error("researcher2", reg) is not None


def test_name_quality_rejects_too_short_or_numeric() -> None:
    reg = OwlRegistry()
    assert name_quality_error("a", reg) is not None
    assert name_quality_error("123", reg) is not None


def test_name_quality_rejects_exact_duplicate() -> None:
    reg = _reg("researcher")
    assert name_quality_error("researcher", reg) is not None


def test_name_quality_accepts_distinct_name() -> None:
    reg = _reg("researcher")
    assert name_quality_error("planner", reg) is None


def test_count_agent_owls() -> None:
    reg = _reg("a", "b")
    assert count_agent_owls(reg) == 2


def test_consent_summary_flags_consequential_and_lists_dropped() -> None:
    summary = consent_summary(
        name="coder",
        role="writes code",
        resolved_tools=frozenset({"read_file", "shell"}),
        dropped=frozenset({"process"}),
        roster=("secretary", "researcher"),
        why="needs to run builds",
    )
    assert "coder" in summary
    assert "shell" in summary and "⚠" in summary  # consequential flagged
    assert "process" in summary  # dropped surfaced
    assert "researcher" in summary  # roster surfaced


def test_max_agent_owls_is_a_positive_int() -> None:
    assert isinstance(MAX_AGENT_OWLS, int) and MAX_AGENT_OWLS > 0
