import pytest

from stackowl.exceptions import OwlNotFoundError
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry


def _m(name, role="r"):
    return OwlAgentManifest(name=name, role=role, system_prompt="p", model_tier="fast")


def test_replace_swaps_existing_in_place():
    r = OwlRegistry()
    r.register(_m("a", role="old"))
    r.replace(_m("a", role="new"))
    assert r.get("a").role == "new"
    assert len(r.list()) == 1


def test_replace_unknown_raises():
    r = OwlRegistry()
    with pytest.raises(OwlNotFoundError):
        r.replace(_m("ghost"))
