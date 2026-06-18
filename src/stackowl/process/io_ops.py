"""ProcessIoMixin — the registry's stdin/stdout I/O half.

Split out of :mod:`stackowl.process.registry` for the B2 ≤300-lines rule: the
lifecycle half (start/poll/kill/list) stays in ``registry.py``; this mixin holds
the captured-output read + interactive stdin operations (read_log / write_stdin /
close). They share the same :class:`ProcessRegistry` state (the handle map, lock,
clock) via the lifecycle half's ``_get_scoped`` helper — one cohesive class
assembled from several files, not a second registry.

Self-healing throughout (B5): a closed pipe / broken transport degrades to a
structured ``False`` (never a raise), and every ``except`` logs.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from stackowl.infra.clock import Clock
from stackowl.infra.observability import log
from stackowl.process.handle import ProcessHandle

if TYPE_CHECKING:  # pragma: no cover

    class _RegistryState:
        """The shared state + lifecycle helper this mixin relies on (typing only)."""

        _clock: Clock
        _lock: Lock

        def _get_scoped(
            self, process_id: str, session_id: str | None
        ) -> ProcessHandle | None: ...

    _Base = _RegistryState
else:
    _Base = object


class ProcessIoMixin(_Base):
    """stdin/stdout I/O operations mixed into :class:`ProcessRegistry`."""

    # ------------------------------------------------------------------- log
    def read_log(self, process_id: str, session_id: str | None = None) -> tuple[str, str] | None:
        """Return ``(stdout, stderr)`` snapshots for a scoped process, or ``None``."""
        handle = self._get_scoped(process_id, session_id)
        if handle is None:
            return None
        handle.last_active = self._clock.monotonic()
        return (handle.stdout_buffer.snapshot(), handle.stderr_buffer.snapshot())

    # ------------------------------------------------------------ write stdin
    async def write_stdin(
        self, process_id: str, data: str, session_id: str | None = None
    ) -> bool:
        """Write ``data`` to a running process's stdin. False if unavailable. B5-safe."""
        handle = self._get_scoped(process_id, session_id)
        if handle is None or not handle.is_running or handle.transport is None:
            return False
        stdin = handle.transport.stdin
        if stdin is None:
            return False
        try:
            stdin.write(data.encode("utf-8"))
            await stdin.drain()
            handle.last_active = self._clock.monotonic()
            return True
        except (OSError, RuntimeError) as exc:  # B5 — closed pipe / broken transport
            log.tool.warning(
                "process.registry.write_stdin: write failed",
                extra={"_fields": {"process_id": process_id, "error": str(exc)}},
            )
            return False

    # ----------------------------------------------------------------- close
    async def close(self, process_id: str, session_id: str | None = None) -> bool:
        """Close a process's stdin (send EOF) without killing it. B5-safe."""
        handle = self._get_scoped(process_id, session_id)
        if handle is None or handle.transport is None or handle.transport.stdin is None:
            return False
        try:
            handle.transport.stdin.close()
            handle.last_active = self._clock.monotonic()
            return True
        except (OSError, RuntimeError) as exc:  # B5
            log.tool.warning(
                "process.registry.close: close failed",
                extra={"_fields": {"process_id": process_id, "error": str(exc)}},
            )
            return False
