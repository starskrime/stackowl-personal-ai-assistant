"""RollingStreamBuffer — a bounded, honest per-stream byte capture.

One buffer captures one stream (stdout OR stderr) of a tracked process. It holds
at most :data:`PER_STREAM_BUFFER_BYTES`; once full, the OLDEST bytes are dropped
so the most recent output is always retained (a tail is what an agent polling a
long-running process actually wants). Truncation is accounted, never silent:
:attr:`total_bytes` (everything ever appended), :attr:`dropped_bytes` (how many
were evicted) and :attr:`truncated` let a reader see the capture is partial.

The registry coordinates the AGGREGATE ceiling across every live process's
buffers; this class only owns its own per-stream bound and exposes
:meth:`live_bytes` / :meth:`release` so the registry can measure and evict.
"""

from __future__ import annotations

from collections import deque

from stackowl.infra.observability import log
from stackowl.process.limits import PER_STREAM_BUFFER_BYTES


class RollingStreamBuffer:
    """A bounded FIFO byte buffer that drops oldest bytes past its cap."""

    def __init__(self, *, max_bytes: int = PER_STREAM_BUFFER_BYTES, name: str = "stream") -> None:
        # 1. ENTRY
        log.tool.debug(
            "process.buffer.__init__: entry",
            extra={"_fields": {"name": name, "max_bytes": max_bytes}},
        )
        self._max_bytes = max(0, int(max_bytes))
        self._name = name
        self._chunks: deque[bytes] = deque()
        self._live_bytes = 0
        self.total_bytes = 0
        self.dropped_bytes = 0
        self.truncated = False

    @property
    def name(self) -> str:
        """The stream label (``stdout`` / ``stderr``) for logging."""
        return self._name

    def append(self, data: bytes) -> None:
        """Append ``data``, evicting oldest bytes if the per-stream cap is exceeded.

        Honest accounting: ``total_bytes`` always grows by ``len(data)``; any
        bytes evicted to stay under the cap bump ``dropped_bytes`` and set
        ``truncated``. Never raises (a capture failure must not kill a reader).
        """
        if not data:
            return
        self.total_bytes += len(data)
        self._chunks.append(data)
        self._live_bytes += len(data)
        # 2. DECISION — over cap? evict oldest whole chunks (FIFO) until under.
        if self._live_bytes > self._max_bytes:
            self._evict_to_cap()

    def _evict_to_cap(self) -> None:
        """Drop oldest chunks until live size fits the cap; account the loss."""
        while self._chunks and self._live_bytes > self._max_bytes:
            oldest = self._chunks.popleft()
            # If a single chunk alone exceeds the cap, keep only its newest tail.
            if len(oldest) > self._max_bytes:
                keep = oldest[-self._max_bytes :] if self._max_bytes else b""
                dropped = len(oldest) - len(keep)
                self._live_bytes -= len(oldest)
                if keep:
                    self._chunks.appendleft(keep)
                    self._live_bytes += len(keep)
                self.dropped_bytes += dropped
            else:
                self._live_bytes -= len(oldest)
                self.dropped_bytes += len(oldest)
        self.truncated = True

    def live_bytes(self) -> int:
        """Bytes currently retained (after per-stream eviction)."""
        return self._live_bytes

    def release(self) -> int:
        """Drop ALL retained bytes (aggregate-ceiling eviction); keep accounting.

        Returns the number of bytes freed. The process keeps running; only the
        already-captured output is released. ``truncated`` is set so a later
        reader still sees the capture is partial.
        """
        freed = self._live_bytes
        if freed:
            log.tool.debug(
                "process.buffer.release: evicting captured bytes (aggregate ceiling)",
                extra={"_fields": {"name": self._name, "freed": freed}},
            )
            self.dropped_bytes += freed
            self.truncated = True
            self._chunks.clear()
            self._live_bytes = 0
        return freed

    def snapshot(self) -> str:
        """Decode the retained bytes to text (replace errors). Never raises."""
        raw = b"".join(self._chunks)
        text = raw.decode("utf-8", errors="replace")
        if self.truncated:
            # Make the truncation visible in the text itself, not just the flags.
            return f"...[{self.dropped_bytes} earlier bytes dropped]...\n{text}"
        return text
