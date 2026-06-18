from stackowl.authz.bounds import BoundsSpec
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.manifest import OwlAgentManifest


def _m(**kw):
    base = dict(name="scout", role="scout", system_prompt="p", model_tier="fast")
    base.update(kw)
    return OwlAgentManifest(**base)


def test_human_owl_omits_agent_only_keys():
    entry = manifest_to_yaml_entry(_m())
    assert entry["origin"] == "human"
    assert "created_by" not in entry  # None -> omitted
    assert "creation_ceiling" not in entry  # None ceiling must NOT serialize as {}


def test_agent_owl_serializes_ceiling_sorted():
    ceiling = BoundsSpec(tools=frozenset({"web_fetch", "read_file"}))
    entry = manifest_to_yaml_entry(_m(origin="agent", created_by="secretary", creation_ceiling=ceiling))
    assert entry["origin"] == "agent"
    assert entry["created_by"] == "secretary"
    assert entry["creation_ceiling"]["tools"] == ["read_file", "web_fetch"]
