from stackowl.infra import decision_ledger as dl


def test_record_lands_and_get_is_non_consuming():
    token = dl.bind()
    try:
        dl.record_decision(
            point="acceptance", verdict="accepted", reason="postcondition observed",
            inputs={"tool": "write_file"}, alternatives_considered=("reject",),
            evidence={"mtime_fresh": True},
        )
        first = dl.get_decisions()
        assert len(first) == 1
        d = first[0]
        assert d.point == "acceptance" and d.verdict == "accepted"
        assert d.reason == "postcondition observed"
        assert d.inputs == {"tool": "write_file"}
        assert d.alternatives_considered == ("reject",)
        assert d.evidence == {"mtime_fresh": True}
        assert len(dl.get_decisions()) == 1  # non-consuming
    finally:
        dl.reset(token)


def test_record_without_bind_is_noop():
    # OFF path: backend skipped bind ⇒ emit sites are silent no-ops, ledger empty.
    dl.record_decision(point="router", verdict="conversational")
    assert dl.get_decisions() == ()


def test_defaults_for_optional_fields():
    token = dl.bind()
    try:
        dl.record_decision(point="recovery", verdict="surrendered")
        d = dl.get_decisions()[0]
        assert d.reason == "" and d.inputs == {} and d.evidence == {}
        assert d.alternatives_considered == ()
    finally:
        dl.reset(token)


def test_multiple_decisions_accumulate_in_order():
    token = dl.bind()
    try:
        dl.record_decision(point="reversibility", verdict="act")
        dl.record_decision(point="recovery", verdict="retried")
        dl.record_decision(point="acceptance", verdict="accepted")
        pts = [d.point for d in dl.get_decisions()]
        assert pts == ["reversibility", "recovery", "acceptance"]
    finally:
        dl.reset(token)


def test_nested_bind_reset_isolation():
    outer = dl.bind()
    try:
        dl.record_decision(point="acceptance", verdict="outer")
        inner = dl.bind()
        try:
            assert dl.get_decisions() == ()  # inner turn starts empty
            dl.record_decision(point="acceptance", verdict="inner")
            assert [d.verdict for d in dl.get_decisions()] == ["inner"]
        finally:
            dl.reset(inner)
        assert [d.verdict for d in dl.get_decisions()] == ["outer"]
    finally:
        dl.reset(outer)


def test_reset_clears_state():
    token = dl.bind()
    dl.record_decision(point="router", verdict="task")
    dl.reset(token)
    assert dl.get_decisions() == ()
