from stackowl.owls.manifest import OwlAgentManifest


def _m(**kw):
    base = dict(name="scout", role="scout", system_prompt="p", model_tier="fast")
    base.update(kw)
    return OwlAgentManifest(**base)


def test_pinned_skills_defaults_empty():
    assert _m().pinned_skills == ()


def test_pinned_skills_round_trip():
    m = _m(skills=("a", "b"), pinned_skills=("a",))
    assert m.pinned_skills == ("a",)
