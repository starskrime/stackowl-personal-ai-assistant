import pytest
from stackowl.pipeline.steps.classify import _parse_turns_to_messages


def test_parse_turns_splits_user_and_assistant():
    rows = ["User: hello\n\nAssistant: hi there",
            "User: find aws practice\n\nAssistant: here are some"]
    msgs = _parse_turns_to_messages(rows)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi there"


def test_parse_turns_tolerates_missing_assistant():
    msgs = _parse_turns_to_messages(["User: just a question"])
    assert msgs[0].role == "user" and msgs[0].content == "just a question"
    assert all(m.content for m in msgs)  # never emits empty-content turns
