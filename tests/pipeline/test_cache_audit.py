"""D01.2 — naming the CAUSE of a cache invalidation, not just the symptom.

``prompt_hash`` (D01.6) already tells you the prompt CHANGED. It cannot tell you
WHICH part changed, and it says nothing at all about the tools array — which
Anthropic renders at position 0, before the system prompt, so a tools array that
varies per turn invalidates everything downstream of it on every single turn.

The tools audit exists to turn D01.3's premise into a measured fact. D01.3
asserts that the per-turn context budget varies the tools array; that assertion
has never been measured. It is measurable here on EVERY protocol — the schemas
are built in execute.py from ``request_text`` — which matters because this
deployment runs an OpenAI-protocol gateway and would observe nothing at all if
the audit lived on the Anthropic path.
"""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.cache_audit import (
    audit_prompt_parts,
    audit_tools_stability,
    reset_audit_state,
)


def _schemas(*names: str) -> list[dict[str, Any]]:
    return [{"name": n, "description": f"the {n} tool"} for n in names]


def setup_function() -> None:
    reset_audit_state()


# ---------------------------------------------------------------------------
# Tools-array stability
# ---------------------------------------------------------------------------

def test_a_stable_tools_array_reports_nothing(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell", "memory"))
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell", "memory"))
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_a_changed_tools_array_is_reported_once_it_changes(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell", "memory"))
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell"))
    changed = [r for r in caplog.records if "tools array CHANGED" in r.message]
    assert len(changed) == 1


def test_the_first_turn_of_a_lane_is_not_a_change(caplog: Any) -> None:
    """There is nothing to have changed FROM. Reporting one would make every new
    conversation look like an invalidation."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("owl:secretary:telegram:dm:1", _schemas("shell"))
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_two_lanes_do_not_contaminate_each_other(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_tools_stability("lane-a", _schemas("shell"))
        audit_tools_stability("lane-b", _schemas("memory"))
        audit_tools_stability("lane-a", _schemas("shell"))
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_reordering_alone_counts_as_a_change() -> None:
    """Order is part of the cached bytes. A reordered array with identical
    members still invalidates position 0, so it must not be normalised away."""
    audit_tools_stability("lane", _schemas("shell", "memory"))
    from stackowl.pipeline.cache_audit import tools_digest

    assert tools_digest(_schemas("shell", "memory")) != tools_digest(
        _schemas("memory", "shell")
    )


def test_an_unhashable_schema_never_raises(caplog: Any) -> None:
    """Measurement must never become an outage."""
    class _Unserialisable:
        pass

    with caplog.at_level("ERROR"):
        audit_tools_stability("lane", [{"name": "x", "fn": _Unserialisable()}])  # must not raise


def test_an_empty_session_key_is_not_tracked(caplog: Any) -> None:
    """Background and utility calls legitimately have no lane. Bucketing them all
    under "" would report a change on every single one."""
    with caplog.at_level("WARNING"):
        audit_tools_stability("", _schemas("shell"))
        audit_tools_stability("", _schemas("memory"))
    assert [r for r in caplog.records if "tools array CHANGED" in r.message] == []


def test_the_tracker_is_bounded() -> None:
    """A long-lived process sees unbounded lanes; the map must not grow forever."""
    from stackowl.pipeline.cache_audit import _LANE_CACHE_MAX, _tools_hashes

    for i in range(_LANE_CACHE_MAX + 50):
        audit_tools_stability(f"lane-{i}", _schemas("shell"))
    assert len(_tools_hashes) <= _LANE_CACHE_MAX


# ---------------------------------------------------------------------------
# Prompt-part audit — which part moved
# ---------------------------------------------------------------------------

def test_prompt_parts_report_the_part_that_changed(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "a", "skills": "b", "profile": "c"})
        audit_prompt_parts("lane", {"persona": "a", "skills": "CHANGED", "profile": "c"})
    changed = [r for r in caplog.records if "prompt part CHANGED" in r.message]
    assert len(changed) == 1
    assert changed[0]._fields["parts"] == ["skills"]


def test_prompt_parts_report_every_part_that_changed(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "a", "skills": "b"})
        audit_prompt_parts("lane", {"persona": "X", "skills": "Y"})
    changed = [r for r in caplog.records if "prompt part CHANGED" in r.message]
    assert changed[0]._fields["parts"] == ["persona", "skills"]


def test_identical_prompt_parts_report_nothing(caplog: Any) -> None:
    with caplog.at_level("WARNING"):
        audit_prompt_parts("lane", {"persona": "a", "skills": "b"})
        audit_prompt_parts("lane", {"persona": "a", "skills": "b"})
    assert [r for r in caplog.records if "prompt part CHANGED" in r.message] == []
