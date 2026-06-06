from stackowl.skills.manifest import SkillManifest


def _m(**kw):
    return SkillManifest(name="alpha", description="d", **kw)


def test_summary_defaults_to_none():
    assert _m().summary is None


def test_summary_accepts_str():
    assert _m(summary="condensed playbook").summary == "condensed playbook"


def test_legacy_skill_without_summary_loads():
    assert _m().summary is None
