from dataclasses import dataclass
from stackowl.skills.instruction_injector import assign_tiers, SkillTier, FULL_FLOOR, SUMMARY_FLOOR


@dataclass
class _Sk:
    name: str
    source: str = "user"
    summary: str | None = "sum"
    description: str = "d"
    when_to_use: str = "w"


def _tier(items, name):
    return next(t for sk, t, _pinned in items if sk.name == name)


def test_floors_map_scores_to_tiers():
    owned = [_Sk("hi"), _Sk("mid"), _Sk("lo")]
    scores = {"hi": 0.9, "mid": 0.30, "lo": 0.05}  # FULL_FLOOR=0.4, SUMMARY_FLOOR=0.2
    items = assign_tiers(owned, scores, pinned=set())
    assert _tier(items, "hi") is SkillTier.FULL
    assert _tier(items, "mid") is SkillTier.SUMMARY
    assert _tier(items, "lo") is SkillTier.CATALOG


def test_pinned_forced_full_even_when_low_score():
    owned = [_Sk("p")]
    items = assign_tiers(owned, {"p": -1.0}, pinned={"p"})
    assert _tier(items, "p") is SkillTier.FULL
    assert items[0][2] is True  # pinned flag


def test_pinned_appear_first_then_score_desc():
    owned = [_Sk("a"), _Sk("b"), _Sk("p")]
    items = assign_tiers(owned, {"a": 0.9, "b": 0.5, "p": 0.1}, pinned={"p"})
    assert items[0][0].name == "p"
    assert [sk.name for sk, _t, _pin in items[1:]] == ["a", "b"]


def test_fallback_scores_none_all_full_manifest_order():
    owned = [_Sk("a"), _Sk("b")]
    items = assign_tiers(owned, None, pinned=set())
    assert all(t is SkillTier.FULL for _sk, t, _pin in items)
    assert [sk.name for sk, _t, _pin in items] == ["a", "b"]


def test_missing_score_sinks_to_catalog():
    owned = [_Sk("x")]
    items = assign_tiers(owned, {}, pinned=set())  # no score for x
    assert _tier(items, "x") is SkillTier.CATALOG


def test_boundary_scores_inclusive():
    owned = [_Sk("f"), _Sk("s")]
    items = assign_tiers(owned, {"f": 0.40, "s": 0.20}, pinned=set())  # exactly on floors
    assert _tier(items, "f") is SkillTier.FULL
    assert _tier(items, "s") is SkillTier.SUMMARY
