from stackowl.infra import recovery_context as rc


def test_record_lands_and_get_is_non_consuming():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="browse_url",
                           recovered_via="http_fetch", user_visible=True)
        first = rc.get_recovery()
        assert len(first) == 1
        e = first[0]
        assert e.kind == "substitution" and e.failed == "browse_url"
        assert e.recovered_via == "http_fetch" and e.user_visible is True
        assert len(rc.get_recovery()) == 1  # non-consuming
    finally:
        rc.reset(token)


def test_record_without_bind_is_noop():
    assert rc.record_recovery(kind="substitution", failed="a",
                              recovered_via="b", user_visible=True) is None
    assert rc.get_recovery() == ()


def test_multiple_events_accumulate_in_order():
    token = rc.bind()
    try:
        rc.record_recovery(kind="substitution", failed="a", recovered_via="b", user_visible=True)
        rc.record_recovery(kind="provider_fallback", failed="c", recovered_via="d", user_visible=False)
        evs = rc.get_recovery()
        assert [e.kind for e in evs] == ["substitution", "provider_fallback"]
        assert evs[1].user_visible is False
    finally:
        rc.reset(token)


def test_reset_clears_state():
    token = rc.bind()
    rc.record_recovery(kind="substitution", failed="a", recovered_via="b", user_visible=True)
    rc.reset(token)
    assert rc.get_recovery() == ()
