"""LS6 — honest skill_manage schema + disjoint preference-vs-skill routing.

These are pure contract assertions on the tool ``parameters`` schema and the
``description`` strings — no provider, no store. They lock the two load-bearing
fixes for the live skill_manage incident: (1) create genuinely requires
non-empty content so the model never sends an empty spec (which fast-failed in
~3ms and tripped the circuit breaker), and (2) the descriptions steer
output-format intent to set_output_preference and procedure-authoring to
skill_manage so the two tools stop colliding.
"""

from __future__ import annotations

from stackowl.tools.knowledge.output_preference import SetOutputPreferenceTool
from stackowl.tools.knowledge.skill_manage import SkillManageTool


def _create_required_fields() -> set[str]:
    """The fields the schema marks required WHEN action='create'."""
    schema = SkillManageTool().parameters
    for clause in schema.get("allOf", []):  # type: ignore[union-attr]
        cond = clause.get("if", {}).get("properties", {}).get("action", {})
        if cond.get("const") == "create":
            return set(clause.get("then", {}).get("required", []))
    return set()


def test_skill_manage_create_requires_content_and_name() -> None:
    """(d) The schema marks content (and name) required for action='create'."""
    required = _create_required_fields()
    assert "content" in required, "create must require content (honest schema)"
    assert "name" in required


def test_skill_manage_create_requirement_does_not_leak_to_other_actions() -> None:
    """(e) content stays OUT of the unconditional required list — delete/enable/
    disable legitimately omit it; only create demands it (via if/then)."""
    schema = SkillManageTool().parameters
    assert schema["required"] == ["action"]  # type: ignore[index]
    # content description states it is mandatory for create so the model knows.
    content_desc = schema["properties"]["content"]["description"].lower()  # type: ignore[index]
    assert "required" in content_desc and "create" in content_desc


def test_descriptions_are_disjoint_for_output_style_intent() -> None:
    """(f) skill_manage steers output-format → set_output_preference; the
    preference tool claims the remember-format intent. Mutually disjoint lanes."""
    skill_desc = SkillManageTool().description.lower()
    pref_desc = SetOutputPreferenceTool().description.lower()

    # skill_manage points output-format intent AT set_output_preference and away
    # from itself.
    assert "set_output_preference" in skill_desc
    assert "format" in skill_desc

    # set_output_preference claims the remember-format intent and disavows the
    # procedure-authoring tool.
    assert "remember" in pref_desc
    assert "format" in pref_desc
    assert "skill_manage" in pref_desc
