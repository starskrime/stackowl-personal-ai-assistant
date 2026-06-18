"""OAuthManager — shared Google OAuth token management with encrypted storage."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("stackowl.integrations")


def _derive_key(master_key: str) -> bytes:
    """Derive a 32-byte AES key from master_key using SHA-256."""
    return hashlib.sha256(master_key.encode()).digest()


class OAuthManager:
    """Manages OAuth credentials with AES-256-GCM encrypted file storage.

    Credentials are stored per-service as base64-encoded ``nonce || ciphertext``
    in a mode-0600 file inside ``credentials_dir``.
    """

    def __init__(self, service_name: str, credentials_dir: Path, master_key: str) -> None:
        log.debug(
            "integrations.oauth_manager.__init__: entry",
            extra={"_fields": {"service": service_name}},
        )
        self._service = service_name
        self._creds_file = credentials_dir / f"{service_name}.enc"
        self._key = _derive_key(master_key)
        credentials_dir.mkdir(parents=True, exist_ok=True)
        log.debug("integrations.oauth_manager.__init__: exit")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, token_data: dict[str, Any]) -> None:
        """Encrypt and persist token_data to disk (AES-256-GCM)."""
        log.debug(
            "integrations.oauth_manager.save: entry",
            extra={"_fields": {"service": self._service}},
        )
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import]

            nonce = os.urandom(12)
            aesgcm = AESGCM(self._key)
            plaintext = json.dumps(token_data).encode()
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            blob = base64.b64encode(nonce + ciphertext).decode()
            self._creds_file.write_text(blob, encoding="utf-8")
            os.chmod(self._creds_file, 0o600)
            log.debug(
                "integrations.oauth_manager.save: exit",
                extra={"_fields": {"service": self._service}},
            )
        except Exception as exc:
            log.error(
                "integrations.oauth_manager.save: failed",
                exc_info=exc,
                extra={"_fields": {"service": self._service}},
            )
            raise

    def load(self) -> dict[str, Any] | None:
        """Decrypt and return stored token data, or None if not found / corrupt."""
        log.debug(
            "integrations.oauth_manager.load: entry",
            extra={"_fields": {"service": self._service}},
        )
        if not self._creds_file.exists():
            log.debug("integrations.oauth_manager.load: decision — no credentials file found")
            return None
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[import]

            raw = base64.b64decode(self._creds_file.read_text(encoding="utf-8"))
            nonce, ciphertext = raw[:12], raw[12:]
            aesgcm = AESGCM(self._key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            result: dict[str, Any] = json.loads(plaintext.decode())
            log.debug(
                "integrations.oauth_manager.load: exit",
                extra={"_fields": {"service": self._service}},
            )
            return result
        except Exception as exc:
            log.error(
                "integrations.oauth_manager.load: failed",
                exc_info=exc,
                extra={"_fields": {"service": self._service}},
            )
            return None

    def delete(self) -> None:
        """Remove stored credentials file."""
        log.debug("integrations.oauth_manager.delete: entry")
        if self._creds_file.exists():
            self._creds_file.unlink()
            log.debug("integrations.oauth_manager.delete: step — credentials file removed")
        log.debug("integrations.oauth_manager.delete: exit")

    def exists(self) -> bool:
        """Return True if a credentials file is present on disk."""
        return self._creds_file.exists()
