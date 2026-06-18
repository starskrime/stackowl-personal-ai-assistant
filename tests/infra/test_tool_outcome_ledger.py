from stackowl.infra import tool_outcome_ledger as tol


def test_consequential_tally_counts_only_consequential_and_write():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="read_file", action_severity="read", success=False)
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        tol.record_tool_outcome(name="write_file", action_severity="write", success=False)
        cons_f, cons_s = tol.consequential_tally()
        assert cons_f == 2
        assert cons_s == 0
    finally:
        tol.reset(token)


def test_consequential_success_counts():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=True)
        cons_f, cons_s = tol.consequential_tally()
        assert cons_f == 1 and cons_s == 1
    finally:
        tol.reset(token)


def test_unbound_is_noop():
    assert tol.record_tool_outcome(name="x", action_severity="consequential", success=False) is None
    assert tol.consequential_tally() == (0, 0)


def test_get_outcomes_returns_recorded():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        outs = tol.get_outcomes()
        assert len(outs) == 1 and outs[0].name == "send_email" and outs[0].success is False
    finally:
        tol.reset(token)


def test_reset_clears():
    token = tol.bind()
    tol.record_tool_outcome(name="x", action_severity="consequential", success=False)
    tol.reset(token)
    assert tol.consequential_tally() == (0, 0)
    assert tol.get_outcomes() == ()
