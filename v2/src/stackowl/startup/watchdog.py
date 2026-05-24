"""WatchdogSec and KeepAlive — stubs for OS service integration (ships in Epic 12)."""

from __future__ import annotations

import logging
import os
import platform

log = logging.getLogger("stackowl.startup")


class WatchdogSec:
    """Notifies systemd watchdog if WATCHDOG_USEC is set; otherwise no-op."""

    def notify(self) -> None:
        log.debug("[watchdog] WatchdogSec.notify: entry")
        if os.environ.get("WATCHDOG_USEC") is None:
            log.info("[watchdog] systemd watchdog not configured — skipping")
            return
        log.info("[watchdog] WatchdogSec.notify: stub — full sd_notify integration ships in Epic 12")


class KeepAlive:
    """Signals launchd KeepAlive on macOS; otherwise no-op."""

    def register(self) -> None:
        log.debug("[keepalive] KeepAlive.register: entry")
        if platform.system() != "Darwin":
            log.info("[keepalive] launchd keepalive not configured — skipping")
            return
        log.info("[keepalive] KeepAlive.register: stub — full launchd integration ships in Epic 12")
