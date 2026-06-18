"""PidManager — writes/removes a PID file with O_EXCL semantics."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from stackowl.exceptions import PidFileExistsError
from stackowl.infra.observability import log


class PidManager:
    """Creates and manages a PID file for the running StackOwl process.

    Uses O_CREAT | O_EXCL to atomically create the file, ensuring only one
    instance can acquire ownership at a time. Stale PID files (pointing at a
    dead process) are silently overwritten.
    """

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def pid_path(self) -> Path:
        from stackowl.paths import StackowlHome
        return StackowlHome.pid_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Create the PID file for the current process.

        Raises:
            PidFileExistsError: if another live process already holds the PID file.
        """
        pid = os.getpid()
        path = self.pid_path

        # 1. ENTRY
        log.infra.debug(
            "[pid_manager] acquire: entry",
            extra={"_fields": {"pid": pid, "path": str(path)}},
        )

        # 2. DECISION — attempt atomic create
        log.infra.debug("[pid_manager] acquire: decision — attempting O_EXCL create")

        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            # 3. STEP — write PID
            log.infra.debug("[pid_manager] acquire: step — writing PID to new file")
            try:
                os.write(fd, str(pid).encode())
            finally:
                os.close(fd)
        except FileExistsError:
            # PID file already exists — check if process is alive
            log.infra.debug("[pid_manager] acquire: decision — PID file exists, checking staleness")
            existing_pid = self._read_existing_pid(path)
            if existing_pid is not None and self._is_running(existing_pid):
                log.infra.warning(
                    "[pid_manager] acquire: conflict — process %d holds PID file", existing_pid
                )
                raise PidFileExistsError(existing_pid)
            # Stale — overwrite
            log.infra.info(
                "[pid_manager] acquire: step — stale PID file detected, overwriting",
                extra={"_fields": {"stale_pid": existing_pid}},
            )
            path.write_text(str(pid), encoding="utf-8")

        # 4. EXIT
        log.infra.debug(
            "[pid_manager] acquire: exit",
            extra={"_fields": {"pid": pid, "path": str(path)}},
        )

    def release(self) -> None:
        """Remove the PID file. Silently ignores missing files."""
        path = self.pid_path

        # 1. ENTRY
        log.infra.debug("[pid_manager] release: entry", extra={"_fields": {"path": str(path)}})

        # 2. DECISION
        log.infra.debug("[pid_manager] release: decision — attempting removal")

        try:
            path.unlink()
            # 3. STEP + 4. EXIT
            log.infra.info("[pid_manager] release: exit — PID file removed")
        except FileNotFoundError:
            log.infra.debug("[pid_manager] release: exit — PID file already gone (no-op)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_existing_pid(path: Path) -> int | None:
        """Read the integer PID from *path*, or return None if unreadable/invalid."""
        try:
            text = path.read_text(encoding="utf-8").strip()
            return int(text)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _is_running(pid: int) -> bool:
        """Return True if *pid* refers to a currently running process."""
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        else:
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                # ProcessLookupError — process does not exist
                # PermissionError — process exists but we lack permissions (still alive)
                return isinstance(sys.exc_info()[1], PermissionError)
