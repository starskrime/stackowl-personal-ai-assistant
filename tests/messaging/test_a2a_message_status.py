"""Tests for A2AMessage status/error fields and A2AResult shape (delegation-healing T2)."""

from stackowl.messaging.a2a import A2AMessage
from stackowl.owls.a2a_delegation import A2AResult


def test_a2a_message_status_error_default_none_and_settable():
    m = A2AMessage.now(from_owl="a", to_owl="b", content="x", message_type="response", trace_id="t")
    assert m.status is None and m.error is None
    m2 = A2AMessage.now(from_owl="a", to_owl="b", content="", message_type="response",
                        trace_id="t", status="child_error", error="boom")
    assert m2.status == "child_error" and m2.error == "boom"


def test_a2a_result_shape():
    r = A2AResult(status="ok", content="hi", child_detail="", resolved_owl="scout")
    assert r.status == "ok" and r.content == "hi" and r.resolved_owl == "scout"
