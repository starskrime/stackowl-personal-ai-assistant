"""Unit tests for _dedup_assistant_history in classify.py.

Story #4 — Stale apology prose is not re-fed/amplified across turns.
When the same assistant message content recurs in the re-fed window, keep only
the most-recent occurrence.  User turns are never touched.
"""
from stackowl.providers.base import Message
from stackowl.pipeline.steps.classify import _dedup_assistant_history


def test_repeated_assistant_message_collapses_to_latest() -> None:
    apology = "Sorry — I've corrected that. Let me know if you need anything else."
    msgs = [
        Message(role="user", content="do X"),
        Message(role="assistant", content=apology),
        Message(role="user", content="ok and Y"),
        Message(role="assistant", content=apology),
        Message(role="user", content="so what?"),
        Message(role="assistant", content=apology),
    ]
    out = _dedup_assistant_history(msgs)
    # The apology survives exactly once (Murat's count-ceiling: <= 1).
    assert sum(1 for m in out if m.role == "assistant" and m.content == apology) == 1
    # User turns are never dropped.
    assert [m.content for m in out if m.role == "user"] == ["do X", "ok and Y", "so what?"]


def test_distinct_assistant_messages_all_kept() -> None:
    msgs = [
        Message(role="assistant", content="A"),
        Message(role="assistant", content="B"),
    ]
    assert [m.content for m in _dedup_assistant_history(msgs)] == ["A", "B"]
