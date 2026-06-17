"""PluginVerifier — integrity verification for a downloaded plugin archive (PLUG-2).

Security posture (the whole point of this module):
  * Verification is PURE HASHING — the archive bytes are NEVER imported, executed,
    or unzipped here. A download is just data until a separate, consent-gated
    install path extracts it. This module can never run third-party code.
  * FAIL-CLOSED — a missing expected digest, a length-mismatched digest, or any
    mismatch raises :class:`PluginVerificationError`. There is no "install anyway"
    path: an unverifiable archive is refused, full stop.
  * Constant-time comparison (``hmac.compare_digest``) so the check does not leak
    the expected digest byte-by-byte via timing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from stackowl.exceptions import SecurityError

log = logging.getLogger("stackowl.plugins")

# A SHA-256 hex digest is exactly 64 hex chars.
_SHA256_HEX_LEN = 64


class PluginVerificationError(SecurityError):
    """Raised when a plugin archive fails integrity verification (fail-closed)."""


class PluginVerifier:
    """Verifies a downloaded plugin archive against an expected SHA-256 digest."""

    def verify_bytes(self, data: bytes, *, expected_sha256: str) -> str:
        """Return the verified hex digest of *data*, or raise.

        NEVER interprets *data* as anything but bytes to hash — no execution.
        """
        log.debug(
            "plugins.verify.verify_bytes: entry",
            extra={"_fields": {"bytes": len(data), "has_expected": bool(expected_sha256)}},
        )
        expected = (expected_sha256 or "").strip().lower()
        # FAIL-CLOSED: no/short expected digest → cannot verify → refuse.
        if len(expected) != _SHA256_HEX_LEN or not all(c in "0123456789abcdef" for c in expected):
            log.warning(
                "plugins.verify.verify_bytes: refused — missing/invalid expected digest",
                extra={"_fields": {"expected_len": len(expected)}},
            )
            raise PluginVerificationError(
                "plugin has no valid sha256 checksum in the index — refusing to "
                "install an unverifiable download (fail-closed)"
            )
        actual = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual, expected):
            log.warning(
                "plugins.verify.verify_bytes: refused — checksum mismatch",
                extra={"_fields": {"expected": expected, "actual": actual}},
            )
            raise PluginVerificationError(
                f"plugin checksum mismatch — expected {expected}, got {actual}; "
                "the download is corrupt or tampered (refusing install)"
            )
        log.info(
            "plugins.verify.verify_bytes: exit — verified",
            extra={"_fields": {"sha256": actual}},
        )
        return actual

    def verify_file(self, path: Path, *, expected_sha256: str) -> str:
        """Read *path* and verify its bytes. Returns the verified hex digest."""
        log.debug(
            "plugins.verify.verify_file: entry",
            extra={"_fields": {"path": str(path)}},
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            log.error(
                "plugins.verify.verify_file: cannot read archive",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            raise PluginVerificationError(f"cannot read downloaded archive: {exc}") from exc
        return self.verify_bytes(data, expected_sha256=expected_sha256)
