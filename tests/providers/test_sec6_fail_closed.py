"""SEC-6 — RateLimiter fails CLOSED on zero-refill (F124) + webhook sig format guard (F141)."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from stackowl.exceptions import RateLimitError
from stackowl.providers.rate_limiter import RateLimiter
from stackowl.webhooks.receiver_helpers import validate_hmac_signature


@pytest.mark.asyncio
class TestRateLimiterFailsClosed:
    async def test_zero_refill_with_deficit_raises_not_grants(self) -> None:
        """A capacity-set limiter with refill_rate=0 must REFUSE past the cap.

        F124: draining the bucket then acquiring again previously LOGGED an error
        and RETURNED (granting the call, failing OPEN). It must instead raise a
        typed RateLimitError — the cap is a real cap, not a hint.
        """
        rl = RateLimiter("p", capacity=1, refill_rate=0.0)
        await rl.acquire()  # drains the single token
        with pytest.raises(RateLimitError):
            await rl.acquire()  # deficit + no refill → fail closed

    async def test_noop_limiter_still_passes_through(self) -> None:
        """capacity=None stays a no-op pass-through (unchanged)."""
        rl = RateLimiter("p", capacity=None, refill_rate=None)
        await rl.acquire()  # must not raise

    async def test_normal_refill_limiter_unaffected(self) -> None:
        rl = RateLimiter.from_rpm("p", 600)  # refill > 0
        await rl.acquire()  # must not raise


class TestWebhookSigFormatGuard:
    def _sig(self, secret: str, body: bytes) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_valid_hex_signature_accepted(self) -> None:
        body = b"payload"
        assert validate_hmac_signature("s3cr3t", body, self._sig("s3cr3t", body)) is True

    def test_valid_with_sha256_prefix_accepted(self) -> None:
        body = b"payload"
        sig = "sha256=" + self._sig("s3cr3t", body)
        assert validate_hmac_signature("s3cr3t", body, sig) is True

    @pytest.mark.parametrize(
        "bad_sig",
        [
            "not-hex-at-all",                       # non-hex chars
            "zzzz" * 16,                            # right length, non-hex
            "abc123",                               # too short
            "a" * 63,                               # one short of 64
            "a" * 65,                               # one over 64
            "deadBEEF" * 8 + "x",                   # 65th char invalid
            "",                                     # empty
        ],
    )
    def test_malformed_signature_rejected_before_compare(self, bad_sig: str) -> None:
        """F141 — a non-hex / wrong-length provided_sig is rejected by the format
        guard (``^[0-9a-fA-F]{64}$``) BEFORE compare_digest is reached."""
        assert validate_hmac_signature("s3cr3t", b"payload", bad_sig) is False
