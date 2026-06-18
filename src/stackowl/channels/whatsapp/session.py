"""WhatsAppSessionManager — persists and restores Playwright browser state.

Session files are stored with restrictive permissions (0o600) since they
contain authentication cookies for WhatsApp Web.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from stackowl.infra.observability import log


class WhatsAppSessionManager:
    """Manages the Playwright browser storage state file for WhatsApp Web.

    The session file contains cookies and localStorage entries that allow
    WhatsApp Web to skip the QR code scan on subsequent launches.

    Security: file is created with 0o600 (owner read/write only).
    """

    def __init__(self, session_dir: str) -> None:
        self._session_dir = session_dir
        log.whatsapp.debug(
            "[whatsapp] session_manager.init: ready",
            extra={"_fields": {"session_dir": session_dir}},
        )

    def session_file_path(self) -> Path:
        """Return the absolute path to the browser storage state JSON file."""
        return Path(self._session_dir) / "state.json"

    def save(self, storage_state: dict[str, Any]) -> None:
        """Write browser storage state to disk with restrictive permissions.

        4-point logging: entry / decision / step / exit.

        Args:
            storage_state: Playwright storage state dict (cookies + origins).
        """
        log.whatsapp.debug("[whatsapp] session_manager.save: entry")
        path = self.session_file_path()
        log.whatsapp.debug(
            "[whatsapp] session_manager.save: decision write_path",
            extra={"_fields": {"path": str(path)}},
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(storage_state), encoding="utf-8")
            os.chmod(path, 0o600)
            log.whatsapp.debug(
                "[whatsapp] session_manager.save: step written_and_secured",
                extra={"_fields": {"size_bytes": path.stat().st_size}},
            )
        except Exception as exc:
            log.whatsapp.error(
                "[whatsapp] session_manager.save: write failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            raise
        log.whatsapp.debug("[whatsapp] session_manager.save: exit")

    def load(self) -> dict[str, Any] | None:
        """Read stored browser state from disk, or return ``None`` if missing.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug("[whatsapp] session_manager.load: entry")
        path = self.session_file_path()
        if not path.exists() or path.stat().st_size == 0:
            log.whatsapp.debug(
                "[whatsapp] session_manager.load: decision no_file",
                extra={"_fields": {"path": str(path)}},
            )
            return None
        log.whatsapp.debug(
            "[whatsapp] session_manager.load: decision reading_file",
            extra={"_fields": {"path": str(path)}},
        )
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            log.whatsapp.debug(
                "[whatsapp] session_manager.load: exit",
                extra={"_fields": {"key_count": len(data)}},
            )
            return data
        except Exception as exc:
            log.whatsapp.error(
                "[whatsapp] session_manager.load: parse failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            return None

    def exists(self) -> bool:
        """Return ``True`` when a non-empty session file is present."""
        path = self.session_file_path()
        return path.exists() and path.stat().st_size > 0

    def clear(self) -> None:
        """Delete the session file if it exists.

        4-point logging: entry / decision / step / exit.
        """
        log.whatsapp.debug("[whatsapp] session_manager.clear: entry")
        path = self.session_file_path()
        if path.exists():
            log.whatsapp.debug(
                "[whatsapp] session_manager.clear: decision deleting",
                extra={"_fields": {"path": str(path)}},
            )
            try:
                path.unlink()
                log.whatsapp.debug("[whatsapp] session_manager.clear: step deleted")
            except Exception as exc:
                log.whatsapp.error(
                    "[whatsapp] session_manager.clear: delete failed",
                    exc_info=exc,
                    extra={"_fields": {"path": str(path)}},
                )
                raise
        else:
            log.whatsapp.debug(
                "[whatsapp] session_manager.clear: decision no_file — nothing to delete"
            )
        log.whatsapp.debug("[whatsapp] session_manager.clear: exit")
