"""Tests for the E4 knowledge guards: the agent_self constant + the
non-interactive default-deny chokepoint.

Covers: interactive=True allows; interactive=False denies; interactive=None
fails closed (denies); the deny carries a structured reason and never raises;
and the agent_self constant stays in lock-step with the StagedFact source_type
Literal (so a tool tagging agent_self can actually be persisted).
"""

from __future__ import annotations

from stackowl.memory.models import StagedFact
from stackowl.tools.knowledge.guards import (
    AGENT_SELF_SOURCE_TYPE,
    GuardDecision,
    deny_if_non_interactive,
)


def test_interactive_true_allows() -> None:
    d = deny_if_non_interactive(interactive=True, operation="memory.add")
    assert isinstance(d, GuardDecision)
    assert d.allowed is True


def test_interactive_false_denies_with_reason() -> None:
    d = deny_if_non_interactive(interactive=False, operation="skill_manage.create")
    assert d.allowed is False
    assert d.reason
    assert "skill_manage.create" in d.reason


def test_interactive_none_fails_closed() -> None:
    d = deny_if_non_interactive(interactive=None, operation="memory.forget")
    assert d.allowed is False
    assert "could not be confirmed" in d.reason


def test_guard_never_raises() -> None:
    # All three branches return a GuardDecision; none raise.
    for val in (True, False, None):
        assert isinstance(
            deny_if_non_interactive(interactive=val, operation="op"),
            GuardDecision,
        )


def test_agent_self_is_a_valid_staged_fact_source_type() -> None:
    # The constant MUST be persistable as a StagedFact source_type, else the
    # memory tool can't tag its writes. This guards the lock-step between the
    # constant, the Literal, and migration 0036.
    fact = StagedFact(
        content="agent-authored",
        source_type=AGENT_SELF_SOURCE_TYPE,  # type: ignore[arg-type]
        source_ref="tool",
        confidence=1.0,
    )
    assert fact.source_type == "agent_self"


def test_agent_self_distinct_from_manual() -> None:
    assert AGENT_SELF_SOURCE_TYPE == "agent_self"
    assert AGENT_SELF_SOURCE_TYPE != "manual"
