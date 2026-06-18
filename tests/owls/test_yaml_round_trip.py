"""Round-trip tests for manifest_to_yaml_entry — bounds/capability_profile/skills."""

from stackowl.authz.bounds import BoundsSpec
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.manifest import OwlAgentManifest


def _m() -> OwlAgentManifest:
    return OwlAgentManifest(
        name="researcher",
        role="research",
        system_prompt="p",
        model_tier="fast",
        tools=["read_file", "web_fetch", "delegate_task"],
        capability_profile=["research"],
        skills=("research",),
        bounds=BoundsSpec(tools=frozenset({"read_file", "web_fetch", "delegate_task"})),
    )


def test_entry_serializes_bounds_capability_profile_skills() -> None:
    e = manifest_to_yaml_entry(_m())
    assert e["capability_profile"] == ["research"]
    assert e["skills"] == ["research"]
    assert sorted(e["bounds"]["tools"]) == ["delegate_task", "read_file", "web_fetch"]
    assert not isinstance(e["bounds"]["tools"], frozenset)


def test_round_trip_reconstructs_equal_manifest() -> None:
    e = manifest_to_yaml_entry(_m())
    rebuilt = OwlAgentManifest(**e)
    assert rebuilt.bounds == _m().bounds
    assert rebuilt.skills == _m().skills
    assert rebuilt.capability_profile == _m().capability_profile


def test_omits_empty_optional_fields() -> None:
    bare = OwlAgentManifest(name="n", role="r", system_prompt="p", model_tier="fast")
    e = manifest_to_yaml_entry(bare)
    assert "bounds" not in e and "skills" not in e and "capability_profile" not in e
