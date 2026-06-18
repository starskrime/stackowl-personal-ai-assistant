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


def test_current_turn_does_not_increment():
    t = SkillFocusTracker()
    assert t.current_turn("o", "s") == 0
    assert t.current_turn("o", "s") == 0   # repeated reads never advance
    t.begin_turn("o", "s")                 # only begin_turn advances
    assert t.current_turn("o", "s") == 1
    assert t.current_turn("o", "s") == 1


def test_skill_view_pattern_does_not_shorten_decay():
    # simulate: assemble turn 1 marks 'a' active; model views 'b' 5x mid-turn (current_turn, no inc);
    # assemble turn 2 -> 'a' should still be within its decay window (diff==1, full bonus).
    t = SkillFocusTracker()
    turn1 = t.begin_turn("o", "s")           # user turn 1
    t.mark_active("o", "s", ["a"], turn1)
    for _ in range(5):                        # 5 skill_view calls in turn 1 (must NOT advance)
        cur = t.current_turn("o", "s")
        t.mark_viewed("o", "s", "b", cur)
    turn2 = t.begin_turn("o", "s")           # user turn 2
    from stackowl.skills.skill_focus import ACTIVE_BONUS
    assert abs(t.bonus_for("o", "s", "a", turn2) - ACTIVE_BONUS) < 1e-9  # 'a' stickiness intact
