from stackowl.infra import retry_ledger as rl


def test_record_lands_and_get_is_non_consuming():
    token = rl.bind()
    try:
        rl.record_retry(kind="circuit_open_skip", provider="powerful-main", detail="OPEN")
        first = rl.get_retry()
        assert len(first) == 1
        e = first[0]
        assert e.kind == "circuit_open_skip" and e.provider == "powerful-main"
        assert e.detail == "OPEN" and e.attempt_number is None
        assert len(rl.get_retry()) == 1  # non-consuming
    finally:
        rl.reset(token)


def test_record_without_bind_is_noop():
    rl.record_retry(kind="circuit_open_skip", provider="a")
    assert rl.get_retry() == ()


def test_multiple_events_accumulate_in_order():
    token = rl.bind()
    try:
        rl.record_retry(kind="circuit_open_skip", provider="a")
        rl.record_retry(kind="tier_escalation", provider="b", attempt_number=2)
        evs = rl.get_retry()
        assert [e.kind for e in evs] == ["circuit_open_skip", "tier_escalation"]
        assert evs[1].attempt_number == 2
    finally:
        rl.reset(token)


def test_nested_bind_reset_isolation():
    outer = rl.bind()
    try:
        rl.record_retry(kind="circuit_open_skip", provider="outer_provider")
        inner = rl.bind()
        try:
            # inner turn starts empty, independent of outer
            assert rl.get_retry() == ()
            rl.record_retry(kind="circuit_open_skip", provider="inner_provider")
            assert [e.provider for e in rl.get_retry()] == ["inner_provider"]
        finally:
            rl.reset(inner)
        # after inner resets, the outer turn's event is intact
        assert [e.provider for e in rl.get_retry()] == ["outer_provider"]
    finally:
        rl.reset(outer)


def test_reset_clears_state():
    token = rl.bind()
    rl.record_retry(kind="circuit_open_skip", provider="a")
    rl.reset(token)
    assert rl.get_retry() == ()
