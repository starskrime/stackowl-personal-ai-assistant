"""TokenBucket — sliding-window rate limiter for the webhook receiver (Story 7.5).

One bucket per ``(source_ip, webhook_source)`` key.  The default budget of
60 requests / 60 seconds intentionally matches typical webhook providers'
inbound burst tolerances (GitHub, Stripe, etc.).

Implementation is a sliding counter rather than a strict token bucket: each
``consume()`` purges samples older than ``window_seconds`` then records a
new one if there is room.  No request bodies or headers ever reach this
class — the only state is the per-key timestamp list.
"""

from __future__ import annotations

import time
from collections import deque

from stackowl.infra.observability import log

_DEFAULT_MAX_TOKENS = 60
_DEFAULT_WINDOW_SEC = 60


class TokenBucket:
    """Per-key sliding-window limiter — never logs request payloads (security)."""

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        window_seconds: int = _DEFAULT_WINDOW_SEC,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be > 0, got {max_tokens}")
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")
        self._max_tokens = max_tokens
        self._window = float(window_seconds)
        self._samples: dict[str, deque[float]] = {}

    def consume(self, key: str) -> bool:
        """Return True if the request is allowed; False if rate-limited.

        ``key`` is the opaque identifier for one logical bucket — typically
        ``f"{remote_ip}:{source}"``.  Never includes the request body.
        """
        now = time.monotonic()
        bucket = self._samples.setdefault(key, deque())

        # 1. PURGE — drop samples that fell out of the window
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        # 2. DECISION — within budget?
        if len(bucket) >= self._max_tokens:
            log.webhook.warning(
                "[webhook] rate_limit.consume: limit reached — request rejected",
                extra={"_fields": {"key_hash": _hash_key(key), "in_window": len(bucket)}},
            )
            return False

        bucket.append(now)
        return True

    def reset(self) -> None:
        """Drop every per-key history bucket — test/admin use."""
        log.webhook.debug(
            "[webhook] rate_limit.reset: clearing buckets",
            extra={"_fields": {"bucket_count": len(self._samples)}},
        )
        self._samples.clear()

    def count(self, key: str) -> int:
        """Return the current in-window request count for ``key``.  Test helper."""
        return len(self._samples.get(key, deque()))


def _hash_key(key: str) -> str:
    """Return a stable short fingerprint of ``key`` for log fields.

    Keys can contain remote IPs — fine for logs, but we still avoid
    echoing the source name into log lines verbatim.
    """
    return f"k{abs(hash(key)) % 10_000_000:07d}"
