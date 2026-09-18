"""Codec + frame round-trip: every frame survives encode -> wire -> decode."""

from __future__ import annotations

import pytest

from stackowl.ipc.codec import FrameDecodeError, decode_frame, encode_frame
from stackowl.ipc.frames import (
    AckFrame,
    ChunkFrame,
    ClarifyAskFrame,
    ClarifyReplyFrame,
    GoodbyeFrame,
    HelloFrame,
    IngressFrame,
    ProgressEventFrame,
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
    ProgressEventFrame(event="pipeline_step_changed", payload={"step_index": 2}),
    ClarifyAskFrame(clarify_id="c1", session_key="s1", question="which one?", trace_id="t1"),
    ClarifyReplyFrame(clarify_id="c1", answer="the first"),
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
