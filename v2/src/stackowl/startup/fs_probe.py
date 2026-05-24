"""FilesystemProbe — checks data dir, log dir, and disk space at startup."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import platformdirs

from stackowl.exceptions import FilesystemProbeError

log = logging.getLogger("stackowl.startup")

_MIN_FREE_BYTES = 100 * 1024 * 1024  # 100 MB


def _data_dir() -> Path:
    raw = os.environ.get("STACKOWL_DATA_DIR")
    return Path(raw) if raw else Path(platformdirs.user_data_dir("stackowl"))


def _log_dir() -> Path:
    raw = os.environ.get("STACKOWL_LOG_DIR")
    return Path(raw) if raw else Path(platformdirs.user_log_dir("stackowl"))


class FilesystemProbe:
    """Validates that required directories exist and are usable before startup."""

    def check(self, dry_run: bool = False) -> None:
        """Run all filesystem checks; raises FilesystemProbeError on failure."""
        log.debug("[startup] fs_probe.check: entry dry_run=%s", dry_run)
        data = _data_dir()
        logs = _log_dir()

        self._ensure_dir(data, "data_dir", dry_run)
        self._ensure_dir(logs, "log_dir", dry_run)
        self._check_disk_space(data)

        log.info("[startup] fs_probe.check: exit — data=%s logs=%s", data, logs)

    def _ensure_dir(self, path: Path, label: str, dry_run: bool) -> None:
        log.debug("[startup] fs_probe: checking %s at %s", label, path)
        if dry_run:
            # In dry_run, validate that the path can exist without creating it.
            check = path if path.exists() else path.parent
            if not check.exists() or not os.access(check, os.W_OK):
                raise FilesystemProbeError(f"{label}_not_creatable", str(path))
        else:
            path.mkdir(parents=True, exist_ok=True)
            if not self._is_writable(path):
                raise FilesystemProbeError(f"{label}_not_writable", str(path))
        log.debug("[startup] fs_probe: %s ok", label)

    def _is_writable(self, path: Path) -> bool:
        probe = path / ".stackowl_probe"
        try:
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return True
        except OSError as exc:
            log.warning("[startup] fs_probe: write test failed at %s: %s", path, exc)
            return False

    def _check_disk_space(self, path: Path) -> None:
        log.debug("[startup] fs_probe: checking disk space at %s", path)
        check = path
        while not check.exists() and check != check.parent:
            check = check.parent
        usage = shutil.disk_usage(check)
        if usage.free < _MIN_FREE_BYTES:
            free_mb = usage.free // (1024 * 1024)
            raise FilesystemProbeError("disk_space_low", f"{path} (free: {free_mb}MB, min: 100MB)")
        log.debug("[startup] fs_probe: disk space ok — free=%dMB", usage.free // (1024 * 1024))
