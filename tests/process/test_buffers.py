"""RollingStreamBuffer — bounded capture + honest truncation accounting (E9-S0)."""

from __future__ import annotations

from stackowl.process.buffers import RollingStreamBuffer


def test_append_under_cap_retains_everything() -> None:
    buf = RollingStreamBuffer(max_bytes=1024)
    buf.append(b"hello ")
    buf.append(b"world")
    assert buf.total_bytes == 11
    assert buf.dropped_bytes == 0
    assert buf.truncated is False
    assert buf.snapshot() == "hello world"


def test_append_over_cap_drops_oldest_and_accounts() -> None:
    buf = RollingStreamBuffer(max_bytes=10)
    buf.append(b"aaaaa")  # 5
    buf.append(b"bbbbb")  # 10 — at cap
    buf.append(b"ccccc")  # 15 → evict the first 5
    assert buf.total_bytes == 15
    assert buf.truncated is True
    assert buf.dropped_bytes == 5
    # Newest bytes retained; the snapshot carries a truncation marker.
    snap = buf.snapshot()
    assert "bbbbbccccc" in snap
    assert "dropped" in snap


def test_single_oversized_chunk_keeps_newest_tail() -> None:
    buf = RollingStreamBuffer(max_bytes=4)
    buf.append(b"0123456789")  # 10 bytes into a 4-byte buffer
    assert buf.truncated is True
    assert buf.dropped_bytes == 6
    assert buf.live_bytes() == 4
    assert "6789" in buf.snapshot()


def test_release_frees_bytes_but_keeps_accounting() -> None:
    buf = RollingStreamBuffer(max_bytes=1024)
    buf.append(b"some captured output")
    freed = buf.release()
    assert freed == len(b"some captured output")
    assert buf.live_bytes() == 0
    assert buf.truncated is True  # release marks the capture partial
    assert buf.dropped_bytes == len(b"some captured output")


def test_empty_append_is_noop() -> None:
    buf = RollingStreamBuffer(max_bytes=16)
    buf.append(b"")
    assert buf.total_bytes == 0
    assert buf.snapshot() == ""
