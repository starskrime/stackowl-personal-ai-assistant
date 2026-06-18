"""stackowl.service — OS service integration (PID management, watchdog, shutdown)."""

from __future__ import annotations

from stackowl.service.pid_manager import PidManager
from stackowl.service.shutdown import ShutdownHandler
from stackowl.service.watchdog import KeepAliveService, WatchdogService

__all__ = [
    "PidManager",
    "ShutdownHandler",
    "WatchdogService",
    "KeepAliveService",
]
