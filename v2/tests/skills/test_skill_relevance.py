from dataclasses import dataclass
from stackowl.skills.skill_focus import SkillFocusTracker
from stackowl.skills.skill_relevance import score_owned_skills


@dataclass
class _Sk:
    name: str
    embedding: list[float] | None


def test_scores_by_cosine_descending():
    owned = [_Sk("a", [1.0, 0.0]), _Sk("b", [0.0, 1.0])]
    scores = score_owned_skills(owned, query_embedding=(1.0, 0.0),
                                tracker=SkillFocusTracker(), owl="o", session="s", turn=1)
    assert scores["a"] > scores["b"]


def test_no_embedding_skill_scores_low():
    owned = [_Sk("a", None)]
    scores = score_owned_skills(owned, query_embedding=(1.0, 0.0),
                                tracker=SkillFocusTracker(), owl="o", session="s", turn=1)
    assert scores["a"] <= 0.0


def test_hysteresis_bonus_lifts_score():
    tr = SkillFocusTracker()
    t1 = tr.begin_turn("o", "s")
    tr.mark_active("o", "s", ["a"], t1)
    owned = [_Sk("a", [0.0, 1.0])]  # orthogonal to query -> ~0 cosine
    t2 = tr.begin_turn("o", "s")
    scores = score_owned_skills(owned, query_embedding=(1.0, 0.0), tracker=tr, owl="o", session="s", turn=t2)
    assert scores["a"] > 0.0  # raw cosine ~0 but ACTIVE bonus lifts it


def test_returns_score_per_owned_skill():
    owned = [_Sk("a", [1.0]), _Sk("b", [1.0]), _Sk("c", None)]
    scores = score_owned_skills(owned, query_embedding=(1.0,),
                                tracker=SkillFocusTracker(), owl="o", session="s", turn=1)
    assert set(scores.keys()) == {"a", "b", "c"}
