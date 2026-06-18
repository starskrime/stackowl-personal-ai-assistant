import pytest
from pydantic import ValidationError
from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest


def _manifest(**kw):
    base = dict(name="scout", role="scout", system_prompt="p", model_tier="fast")
    base.update(kw)
    return OwlAgentManifest(**base)


def test_provenance_defaults_to_human_unclamped():
    m = _manifest()
    assert m.origin == "human"
    assert m.created_by is None
    assert m.creation_ceiling is None


def test_agent_origin_fields_round_trip():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    m = _manifest(origin="agent", created_by="secretary", creation_ceiling=ceiling)
    assert m.origin == "agent"
    assert m.created_by == "secretary"
    assert m.creation_ceiling == ceiling


def test_origin_rejects_unknown_value():
    with pytest.raises(ValidationError):
        _manifest(origin="rogue")


def test_manifest_is_frozen():
    m = _manifest()
    with pytest.raises(ValidationError):
        m.origin = "agent"  # type: ignore[misc]
