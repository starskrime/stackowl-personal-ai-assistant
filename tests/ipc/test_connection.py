"""``FrameConnection._iter`` never silently skips a bad line (Spec 2.3).

Before this story an undecodable line (malformed JSON, or JSON with an
unknown ``type``) was a bare ``continue`` inside ``_iter`` -- a half-upgraded
install could corrupt data or drop messages with no signal at all. Now every
bad line logs one WARNING (naming the ``type`` best-effort, when
extractable) before the connection moves on -- and it DOES move on: a
corrupt line never kills the stream, it just never yields a Frame for it.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from stackowl.ipc.codec import encode_frame
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import AckFrame


class _NullWriter:
    """FrameConnection never calls .send()/.aclose() in this test — unused."""


def _reader_with(*lines: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line)
    reader.feed_eof()
    return reader


async def test_unknown_type_line_warns_and_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    reader = _reader_with(
        b'{"type":"no_such_frame"}\n',
        encode_frame(AckFrame(ref="ok", status="ok")),
    )
    conn = FrameConnection(reader, _NullWriter())  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="stackowl.ipc"):
        frames = [f async for f in conn]

    assert [f.ref for f in frames if isinstance(f, AckFrame)] == ["ok"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no_such_frame" in warnings[0].getMessage() or getattr(
        warnings[0], "_fields", {}
    ).get("frame_type") == "no_such_frame"


async def test_malformed_json_line_warns_with_no_type_and_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reader = _reader_with(
        b"not json at all\n",
        encode_frame(AckFrame(ref="ok2", status="ok")),
    )
    conn = FrameConnection(reader, _NullWriter())  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="stackowl.ipc"):
        frames = [f async for f in conn]

    assert [f.ref for f in frames if isinstance(f, AckFrame)] == ["ok2"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert getattr(warnings[0], "_fields", {}).get("frame_type") is None


async def test_connection_keeps_yielding_after_multiple_bad_lines() -> None:
    """The whole point: a bad line never kills the stream."""
    reader = _reader_with(
        b'{"type":"unknown_one"}\n',
        b"also not json\n",
        encode_frame(AckFrame(ref="a", status="ok")),
        b'{"type":"unknown_two"}\n',
        encode_frame(AckFrame(ref="b", status="ok")),
    )
    conn = FrameConnection(reader, _NullWriter())  # type: ignore[arg-type]

    frames = [f async for f in conn]
    assert [f.ref for f in frames if isinstance(f, AckFrame)] == ["a", "b"]
