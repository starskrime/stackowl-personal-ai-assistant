from stackowl.skills.skill_focus import SkillFocusTracker, ACTIVE_BONUS, VIEW_BONUS


def test_no_history_zero_bonus():
    t = SkillFocusTracker()
    turn = t.begin_turn("owl", "sess")
    assert t.bonus_for("owl", "sess", "x", turn) == 0.0


def test_active_bonus_next_turn_is_full_then_decays():
    t = SkillFocusTracker()
    turn1 = t.begin_turn("owl", "sess")
    t.mark_active("owl", "sess", ["x"], turn1)
    turn2 = t.begin_turn("owl", "sess")
    assert abs(t.bonus_for("owl", "sess", "x", turn2) - ACTIVE_BONUS) < 1e-9
    turn3 = t.begin_turn("owl", "sess")
    assert 0.0 < t.bonus_for("owl", "sess", "x", turn3) < ACTIVE_BONUS


def test_view_bonus_stronger_than_active_and_max_not_sum():
    t = SkillFocusTracker()
    turn1 = t.begin_turn("owl", "sess")
    t.mark_active("owl", "sess", ["x"], turn1)
    t.mark_viewed("owl", "sess", "x", turn1)
    turn2 = t.begin_turn("owl", "sess")
    assert abs(t.bonus_for("owl", "sess", "x", turn2) - VIEW_BONUS) < 1e-9


def test_bonus_zero_after_decay_window():
    t = SkillFocusTracker()
    turn1 = t.begin_turn("owl", "sess")
    t.mark_active("owl", "sess", ["x"], turn1)
    last = 0.0
    for _ in range(6):
        tn = t.begin_turn("owl", "sess")
        last = t.bonus_for("owl", "sess", "x", tn)
    assert last == 0.0


def test_session_and_owl_isolation():
    t = SkillFocusTracker()
    turn1 = t.begin_turn("owl", "sess")
    t.mark_active("owl", "sess", ["x"], turn1)
    other = t.begin_turn("owl", "other")
    assert t.bonus_for("owl", "other", "x", other) == 0.0
    other_owl = t.begin_turn("owl2", "sess")
    assert t.bonus_for("owl2", "sess", "x", other_owl) == 0.0


def test_singleton_exists_and_clears():
    from stackowl.skills.skill_focus import FOCUS_TRACKER
    FOCUS_TRACKER.clear_all()
    turn = FOCUS_TRACKER.begin_turn("o", "s")
    assert FOCUS_TRACKER.bonus_for("o", "s", "x", turn) == 0.0
