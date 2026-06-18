"""ProcessCheckpoint — crash-recovery persistence + boot-time reconcile.

The :class:`ProcessRegistry` owns its OWN lifecycle (Fork B): it persists the
metadata of every tracked process to ``~/.stackowl/process_registry.json`` (atomic
write) so that, after a restart, it can RECONCILE — probe each persisted pid's
liveness and either re-adopt the still-alive ones (status ``running``, but with no
output pipe — detached) or mark the dead ones ``exited``. The Supervisor is NOT
used for this; a process exiting is the normal terminal state, never a fault.

Self-healing throughout: a missing or CORRUPT checkpoint file never crashes boot —
it is logged and treated as an empty set ([[feedback_no_hidden_errors]] heal path).
The on-disk file lives under ``~/.stackowl/`` ([[feedback_all_state_in_home]]),
never inside the project dir, and the write is atomic (temp + ``os.replace``) so a
crash mid-write cannot leave a half-file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.process.kill_platform import is_pid_alive


@dataclass
class CheckpointEntry:
    """One persisted process record (the minimum needed to reconcile at boot)."""

    process_id: str
    pid: int | None
    command: list[str]
    session_id: str
    created_at: float
    status: str


@dataclass
class ReconcileResult:
    """Outcome of a boot-time reconcile: which persisted pids were alive vs dead."""

    adopted: list[CheckpointEntry]  # still-alive — re-adopt as detached running
    exited: list[CheckpointEntry]  # dead — record terminal


class ProcessCheckpoint:
    """Persists tracked-process metadata and reconciles it against live pids."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or (StackowlHome.home() / "process_registry.json")
        log.tool.debug(
            "process.checkpoint.__init__: entry",
            extra={"_fields": {"path": str(self._path)}},
        )

    @property
    def path(self) -> Path:
        """The on-disk checkpoint location (under ~/.stackowl/)."""
        return self._path

    def save(self, entries: list[CheckpointEntry]) -> None:
        """Atomically persist ``entries``. Never raises (B5 — a save failure is logged).

        Only RUNNING processes are worth persisting; the caller filters. Atomic
        write = temp file in the same dir + ``os.replace`` so a crash mid-write
        cannot corrupt the live file.
        """
        # 1. ENTRY
        log.tool.debug(
            "process.checkpoint.save: entry",
            extra={"_fields": {"count": len(entries)}},
        )
        payload = [
            {
                "process_id": e.process_id,
                "pid": e.pid,
                "command": e.command,
                "session_id": e.session_id,
                "created_at": e.created_at,
                "status": e.status,
            }
            for e in entries
        ]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".proc_ckpt_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                os.replace(tmp, self._path)
            finally:
                # If replace failed the temp file may linger — clean it up.
                if os.path.exists(tmp):
                    with _suppress_os():
                        os.unlink(tmp)
        except OSError as exc:  # B5 — a checkpoint failure must not crash a spawn
            log.tool.warning(
                "process.checkpoint.save: write failed — continuing uncheckpointed",
                extra={"_fields": {"error": str(exc), "path": str(self._path)}},
            )

    def load(self) -> list[CheckpointEntry]:
        """Load persisted entries; a missing/corrupt file yields an empty list.

        Self-healing: any read/parse error is logged and treated as "start empty"
        rather than crashing boot.
        """
        if not self._path.exists():
            log.tool.debug("process.checkpoint.load: no file — empty", extra={"_fields": {}})
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # corrupt/unreadable → start empty
            log.tool.warning(
                "process.checkpoint.load: unreadable/corrupt — starting empty",
                extra={"_fields": {"error": str(exc), "path": str(self._path)}},
            )
            return []
        if not isinstance(raw, list):
            log.tool.warning(
                "process.checkpoint.load: unexpected shape — starting empty",
                extra={"_fields": {"type": type(raw).__name__}},
            )
            return []
        entries: list[CheckpointEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cmd = item.get("command")
            entries.append(
                CheckpointEntry(
                    process_id=str(item.get("process_id", "")),
                    pid=item.get("pid") if isinstance(item.get("pid"), int) else None,
                    command=[str(c) for c in cmd] if isinstance(cmd, list) else [],
                    session_id=str(item.get("session_id", "")),
                    created_at=float(item.get("created_at", 0.0) or 0.0),
                    status=str(item.get("status", "running")),
                )
            )
        log.tool.debug("process.checkpoint.load: exit", extra={"_fields": {"count": len(entries)}})
        return entries

    def reconcile(self) -> ReconcileResult:
        """Probe each persisted pid; partition into still-alive vs dead.

        A pid that responds to a liveness probe is re-adopted (the process
        survived the restart — detached, no output pipe); a dead pid is recorded
        as ``exited``. Cross-platform via :func:`is_pid_alive`. Never raises.
        """
        # 1. ENTRY
        entries = self.load()
        log.tool.debug(
            "process.checkpoint.reconcile: entry",
            extra={"_fields": {"persisted": len(entries)}},
        )
        adopted: list[CheckpointEntry] = []
        exited: list[CheckpointEntry] = []
        for entry in entries:
            # 2. DECISION — alive pid → adopt; dead pid → mark exited.
            if entry.pid is not None and is_pid_alive(entry.pid):
                adopted.append(entry)
            else:
                entry.status = "exited"
                exited.append(entry)
        # 4. EXIT
        log.tool.info(
            "process.checkpoint.reconcile: exit",
            extra={"_fields": {"adopted": len(adopted), "exited": len(exited)}},
        )
        return ReconcileResult(adopted=adopted, exited=exited)


class _suppress_os:
    """Tiny context manager that swallows OSError (temp-file cleanup)."""

    def __enter__(self) -> _suppress_os:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return isinstance(exc, OSError)
