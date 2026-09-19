"""Codec + frame round-trip: every frame survives encode -> wire -> decode."""

from __future__ import annotations

import pydantic
import pytest

from stackowl.ipc.codec import FrameDecodeError, decode_frame, encode_frame
from stackowl.ipc.frames import (
    AckFrame,
    ChunkFrame,
    ClarifyAskFrame,
    ClarifyReplyFrame,
    ConsentRequestFrame,
    ConsentResponseFrame,
    GoodbyeFrame,
    HelloFrame,
    IngressFrame,
    JournalEventFrame,
    RestartNoticeFrame,
    SendTextFrame,
)

ALL_FRAMES = [
    HelloFrame(sender_pid=1234, highest_migration=1, registry_digest="x"),
    GoodbyeFrame(reason="shutdown"),
    RestartNoticeFrame(reason="code change", grace_seconds=120.0),
    IngressFrame(text="hi", session_key="s1", channel="cli", trace_id="t1"),
    IngressFrame(
        text="reply", session_key="s1", channel="telegram", trace_id="t2",
        chat_id=42, is_reply=True,
    ),
    ChunkFrame(content="hello", is_final=False, chunk_index=0, trace_id="t1", owl_name="owl"),
    ChunkFrame(content="", is_final=True, chunk_index=-1, trace_id="t1", owl_name=""),
    ChunkFrame(
        content="status", is_final=False, chunk_index=1, trace_id="t1",
        owl_name="owl", kind="progress", target="chan:thread", is_floor=False,
    ),
    SendTextFrame(channel="telegram", text="ping", target=42),
    JournalEventFrame(
        cursor=7,
        event_id="ev-1",
        event_type="task.enqueued",
        schema_version=1,
        occurred_at="2026-09-17T00:00:00+00:00",
        actor_kind="autonomous",
        actor_id="owner",
        target_kind="owner",
        target_id="task-1",
        outcome="pending",
        record_ref={"kind": "sqlite", "locator": {"table": "tasks", "task_id": "task-1"}},
        attrs={"trigger_kind": "chat", "depends_on_count": 0, "max_attempts": 3},
        trace_id="t1",
        duration_ms=12,
    ),
    ClarifyAskFrame(clarify_id="c1", session_key="s1", question="which one?", trace_id="t1"),
    # Story 3.6 — needs_you_item_id carried on a blocking clarify's frame.
    ClarifyAskFrame(
        clarify_id="c2", session_key="s1", question="which one?", trace_id="t1",
        needs_you_item_id="item-abc",
    ),
    ClarifyReplyFrame(clarify_id="c1", answer="the first"),
    ConsentRequestFrame(
        consent_id="cr-1", channel="telegram", tool_name="shell", session_key="s1",
        reply_target=72055773, category="catastrophic", summary="run ls",
    ),
    # Negative reply_target — a Telegram group/thread id (Story 3.5).
    ConsentRequestFrame(
        consent_id="cr-2", channel="telegram", tool_name="shell", session_key="s1",
        reply_target=-100123456789,
    ),
    # Story 3.6 — item_id carried across the link.
    ConsentRequestFrame(
        consent_id="cr-3", channel="telegram", tool_name="shell", session_key="s1",
        reply_target=72055773, item_id="item-xyz",
    ),
    ConsentResponseFrame(consent_id="cr-1", scope="session"),
    AckFrame(ref="t1", status="deferred", detail="quiescing"),
]


@pytest.mark.parametrize("frame", ALL_FRAMES, ids=lambda f: f.type)
def test_round_trip(frame) -> None:
    assert decode_frame(encode_frame(frame)) == frame


def test_encoded_frame_is_single_newline_terminated_line() -> None:
    wire = encode_frame(ChunkFrame(
        content="multi\nline\ncontent", is_final=False, chunk_index=0,
        trace_id="t1", owl_name="owl",
    ))
    # Exactly one newline — the terminator. Embedded \n is JSON-escaped, not literal.
    assert wire.count(b"\n") == 1
    assert wire.endswith(b"\n")


def test_newline_in_content_survives_round_trip() -> None:
    frame = ChunkFrame(
        content="line1\nline2\n", is_final=False, chunk_index=0,
        trace_id="t1", owl_name="owl",
    )
    assert decode_frame(encode_frame(frame)).content == "line1\nline2\n"


def test_decode_rejects_unknown_type() -> None:
    with pytest.raises(FrameDecodeError):
        decode_frame(b'{"type": "no_such_frame"}\n')


def test_decode_error_names_the_unknown_type_best_effort() -> None:
    """Spec 2.3 — the reader can log WHICH type it couldn't decode instead of
    a silent skip; a best-effort JSON extraction backs it, not a full parse."""
    with pytest.raises(FrameDecodeError) as exc_info:
        decode_frame(b'{"type":"no_such_frame"}\n')
    assert exc_info.value.frame_type == "no_such_frame"


def test_decode_rejects_malformed_json() -> None:
    with pytest.raises(FrameDecodeError):
        decode_frame(b"not json at all\n")


def test_decode_rejects_empty_line() -> None:
    with pytest.raises(FrameDecodeError):
        decode_frame(b"\n")


def test_decode_tolerates_missing_trailing_newline() -> None:
    frame = AckFrame(ref="t9")
    wire_no_nl = encode_frame(frame).rstrip(b"\n")
    assert decode_frame(wire_no_nl) == frame


def test_consent_request_frame_rejects_unknown_field() -> None:
    """AC1 — ConsentRequestFrame stays typed with ``extra=forbid``: an
    unexpected keyword argument must raise, not silently pass through."""
    with pytest.raises(pydantic.ValidationError):
        ConsentRequestFrame(
            consent_id="cr-9", channel="telegram", tool_name="shell",
            session_key="s1", not_a_real_field="surprise",
        )


def test_consent_request_frame_without_reply_target_decodes_to_none() -> None:
    """Story 3.5 — an old-protocol peer's wire bytes omit the key entirely;
    the field default fills it in as None rather than raising."""
    wire = (
        b'{"type":"consent_request","consent_id":"cr-3","channel":"telegram",'
        b'"tool_name":"shell","session_key":"s1"}\n'
    )
    frame = decode_frame(wire)
    assert isinstance(frame, ConsentRequestFrame)
    assert frame.reply_target is None


def test_consent_request_frame_without_item_id_decodes_to_none() -> None:
    """Story 3.6 — an old-protocol (pre-3.6) peer's wire bytes omit
    ``item_id`` entirely; the field default fills it in as None."""
    wire = (
        b'{"type":"consent_request","consent_id":"cr-4","channel":"telegram",'
        b'"tool_name":"shell","session_key":"s1","reply_target":42}\n'
    )
    frame = decode_frame(wire)
    assert isinstance(frame, ConsentRequestFrame)
    assert frame.item_id is None
