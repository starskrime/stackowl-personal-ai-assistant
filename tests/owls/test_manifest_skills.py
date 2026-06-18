from stackowl.owls.manifest import OwlAgentManifest


def _base(**kw):
    return OwlAgentManifest(name="n", role="r", system_prompt="p", model_tier="fast", **kw)


def test_skills_defaults_to_empty_tuple():
    assert _base().skills == ()


def test_skills_accepts_tuple_and_is_frozen():
    m = _base(skills=("research", "writing"))
    assert m.skills == ("research", "writing")


def test_legacy_manifest_without_skills_still_valid():
    assert _base().skills == ()
