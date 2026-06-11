from stackowl.pipeline import lesson_context as lc


def _surfaced():
    return (
        lc.SurfacedLesson(lesson_id="L1", source_type="tool_heuristic",
                          content="browse_url tends to fail on PDF hosts", similarity=0.9),
        lc.SurfacedLesson(lesson_id="L2", source_type="reflection",
                          content="prefer fetch over scrape", similarity=0.7),
    )


def test_record_known_id_lands_in_sink_with_summary():
    token = lc.bind()
    try:
        lc.set_surfaced(_surfaced())
        matched = lc.record_applied("L1", "used the fetch tool instead of browse_url")
        assert matched is not None and matched.lesson_id == "L1"
        applied = lc.drain_applied()
        assert len(applied) == 1
        assert applied[0].lesson_id == "L1"
        assert applied[0].what_you_did == "used the fetch tool instead of browse_url"
        assert applied[0].lesson_summary == "browse_url tends to fail on PDF hosts"
    finally:
        lc.reset(token)


def test_record_unknown_id_is_recorded_with_null_summary():
    token = lc.bind()
    try:
        lc.set_surfaced(_surfaced())
        matched = lc.record_applied("L9", "did a thing")
        assert matched is None
        applied = lc.drain_applied()
        assert len(applied) == 1 and applied[0].lesson_summary is None
        assert applied[0].what_you_did == "did a thing"
    finally:
        lc.reset(token)


def test_record_without_bind_is_noop():
    assert lc.record_applied("L1", "x") is None
    assert lc.drain_applied() == ()


def test_reset_clears_state():
    token = lc.bind()
    try:
        lc.set_surfaced(_surfaced())
        lc.record_applied("L1", "x")
    finally:
        lc.reset(token)
    assert lc.drain_applied() == ()
    assert lc.get_surfaced() == ()


def test_multiple_records_accumulate_and_surfaced_roundtrips():
    token = lc.bind()
    try:
        lessons = _surfaced()
        lc.set_surfaced(lessons)
        assert lc.get_surfaced() == lessons
        lc.record_applied("L1", "used fetch instead of browse_url")
        lc.record_applied("L2", "chose fetch over scrape as instructed")
        applied = lc.drain_applied()
        assert len(applied) == 2
        assert applied[0].lesson_id == "L1"
        assert applied[1].lesson_id == "L2"
        assert applied[0].lesson_summary == "browse_url tends to fail on PDF hosts"
        assert applied[1].lesson_summary == "prefer fetch over scrape"
    finally:
        lc.reset(token)
