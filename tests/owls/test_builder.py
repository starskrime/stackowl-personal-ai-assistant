import pytest

from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder


def _build(**kw):
    spec = OwlSpec(name=kw.pop("name", "r"), role=kw.pop("role", "research"),
                   model_tier=kw.pop("tier", "fast"), **kw)
    return SpecialistOwlBuilder().build(spec)


def test_preset_specialist_has_bounds_excluding_shell():
    m = _build(name="rsr", preset="researcher")
    assert m.bounds is not None
    assert "shell" not in m.bounds.tools
    assert "read_file" in m.bounds.tools


def test_boundary_router_tools_always_included():
    """ESC-34: `delegate_task` left ROUTER_TOOLS on 2026-08-23; discovery and the
    APPEAL remain, so a narrow owl is never stranded and can always ask."""
    m = _build(name="rsr", preset="researcher")
    assert {"tool_search", "tool_describe", "owl_build", "owls_list"} <= m.bounds.tools
    assert "delegate_task" not in m.bounds.tools


def test_persona_points_at_a_tool_the_owl_ACTUALLY_HOLDS():
    """This test PASSING is what caught the change being incomplete.

    Removing `delegate_task` from ROUTER_TOOLS without touching the persona would
    have left every new owl instructed to use a tool it no longer holds — the exact
    defect ESC-34 is about, one layer up. The compass now points at the appeal.
    """
    m = _build(name="rsr", preset="researcher")
    assert "delegate_task" not in m.system_prompt
    assert "owl_build" in m.system_prompt
    assert "owl_build" in m.bounds.tools, "the persona must name a tool it holds"


def test_explicit_tools_validated_against_catalog_drops_unknown():
    m = _build(name="x", explicit_tools=("read_file", "totally_fake_tool"),
               valid_tools=frozenset({"read_file"}))
    assert "read_file" in m.bounds.tools
    assert "totally_fake_tool" not in m.bounds.tools


def test_capability_profile_and_skills_carried():
    m = _build(name="rsr", preset="researcher", skills=("research",))
    assert m.capability_profile == ["research"]
    assert m.skills == ("research",)


def test_no_preset_no_tools_makes_unbounded_general_owl():
    m = _build(name="plain")
    assert m.bounds is None


def test_explicit_system_prompt_overrides_generated_persona():
    m = _build(name="rsr", preset="researcher", system_prompt="custom")
    assert m.system_prompt == "custom"


def test_requires_preset_xor_explicit():
    with pytest.raises(ValueError):
        _build(name="x", preset="researcher", explicit_tools=("read_file",))


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        _build(name="x", preset="nonexistent")
