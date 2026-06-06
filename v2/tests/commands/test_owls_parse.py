import pytest

from stackowl.commands.owls_helpers import build_owl_manifest, parse_add_args
from stackowl.exceptions import CommandParseError


def test_parse_preset_and_skills():
    p = parse_add_args("rsr --role research --tier fast --preset researcher --skills research,writing")
    assert p["preset"] == "researcher"
    assert p["skills"] == ["research", "writing"]


def test_parse_capability_profile_and_system_prompt():
    p = parse_add_args('x --role r --tier fast --capability-profile research --system-prompt "be terse"')
    assert p["capability_profile"] == ["research"]
    assert p["system_prompt"] == "be terse"


def test_build_from_preset_delegates_to_builder_with_bounds():
    p = parse_add_args("rsr --role research --tier fast --preset researcher")
    m = build_owl_manifest(p)
    assert m.bounds is not None and "shell" not in m.bounds.tools
    assert "delegate_task" in m.bounds.tools


def test_build_bare_owl_still_unbounded():
    p = parse_add_args("plain --role helper --tier fast")
    assert build_owl_manifest(p).bounds is None


def test_unknown_preset_rejected():
    p = parse_add_args("x --role r --tier fast --preset nope")
    with pytest.raises((ValueError, CommandParseError)):
        build_owl_manifest(p)
