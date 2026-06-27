"""Watchdog — sd_notify WATCHDOG=1 integration for systemd, and launchd KeepAlive stub."""

from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable

from stackowl.infra.observability import log

# A liveness gate: returns True if the process is genuinely healthy enough to keep
# telling systemd "alive". May be sync or async. Returning False suppresses the
# WATCHDOG=1 ping so systemd's watchdog-timeout can restart a wedged process.
LivenessCheck = Callable[[], bool] | Callable[[], Awaitable[bool]]

_WATCHDOG_INTERVAL_S = 30


class WatchdogService:
    """Sends ``sd_notify WATCHDOG=1`` pings to systemd on a 30-second interval.

    If ``WATCHDOG_USEC`` is not set (non-systemd environment), the service
    silently no-ops so the same startup code works on macOS and Windows.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._liveness_check: LivenessCheck | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, *, liveness_check: LivenessCheck | None = None) -> None:
        """Start the watchdog ping loop if running under systemd.

        ``liveness_check`` (F-85) is an optional sync-or-async predicate consulted
        before EACH ping. When it returns ``False`` (a critical subsystem is down)
        the ``WATCHDOG=1`` ping is SKIPPED so systemd's watchdog-timeout restarts
        the unit — closing the "deadlocked-but-spinning loop reports healthy" gap.
        A ``None`` check preserves the prior unconditional-ping behaviour. The gate
        fails OPEN: if the check itself raises, the ping still fires (a broken probe
        must not trigger a false restart)."""
        # 1. ENTRY
        log.infra.debug(
            "[watchdog] start: entry",
            extra={"_fields": {"liveness_gated": liveness_check is not None}},
        )
        self._liveness_check = liveness_check

        watchdog_usec = os.environ.get("WATCHDOG_USEC")

        # 2. DECISION
        if not watchdog_usec:
            log.infra.info("[watchdog] systemd watchdog not configured — skipping")
            return

        interval_s = min(_WATCHDOG_INTERVAL_S, int(watchdog_usec) / 1_000_000 / 2)
        log.infra.debug(
            "[watchdog] start: decision — systemd watchdog active",
            extra={"_fields": {"watchdog_usec": watchdog_usec, "ping_interval_s": interval_s}},
        )

        # 3. STEP — schedule asyncio task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.infra.warning("[watchdog] start: no running event loop — watchdog not started")
            return

        self._task = loop.create_task(self._ping_loop(interval_s), name="watchdog-ping")

        # 4. EXIT
        log.infra.info(
            "[watchdog] start: exit — watchdog task created",
            extra={"_fields": {"interval_s": interval_s}},
        )

    def send_ready(self) -> None:
        """Send ``READY=1`` to systemd ONCE, after the service is serving-ready.

        Under ``Type=notify`` systemd holds dependents until this fires. Must be
        called AFTER all assembly (migrations, adapters) so systemd never marks the
        unit ready while startup could still fail. Off-systemd (no ``WATCHDOG_USEC``
        / no ``NOTIFY_SOCKET``) ``_sd_notify`` self-skips via ``systemd-notify``
        absence — a clean no-op on macOS/Windows/Jetson (all-hardware mandate)."""
        # 1. ENTRY
        log.infra.debug("[watchdog] send_ready: entry")
        if not os.environ.get("NOTIFY_SOCKET") and not os.environ.get("WATCHDOG_USEC"):
            # 2. DECISION — not under systemd notify; nothing to signal.
            log.infra.debug("[watchdog] send_ready: not under systemd — skipping READY=1")
            return
        # 3. STEP + 4. EXIT
        self._sd_notify("READY=1")
        log.infra.info("[watchdog] send_ready: exit — READY=1 sent")

    def stop(self) -> None:
        """Cancel the watchdog ping task."""
        # 1. ENTRY
        log.infra.debug("[watchdog] stop: entry")

        # 2. DECISION
        if self._task is None:
            log.infra.debug("[watchdog] stop: exit — no task to cancel")
            return

        # 3. STEP
        self._task.cancel()
        self._task = None

        # 4. EXIT
        log.infra.info("[watchdog] stop: exit — task cancelled")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ping_loop(self, interval_s: float) -> None:
        """Emit ``sd_notify WATCHDOG=1`` every *interval_s* seconds."""
        log.infra.debug(
            "[watchdog] _ping_loop: entry",
            extra={"_fields": {"interval_s": interval_s}},
        )
        try:
            while True:
                await asyncio.sleep(interval_s)
                # F-85 — gate the ping on a real liveness signal. A wedged-but-
                # spinning loop must NOT keep reporting healthy.
                if not await self._is_live():
                    log.infra.warning(
                        "[watchdog] _ping_loop: liveness DOWN — skipping WATCHDOG=1 "
                        "ping so systemd can restart the unit",
                    )
                    continue
                self._sd_notify("WATCHDOG=1")
        except asyncio.CancelledError:
            log.infra.debug("[watchdog] _ping_loop: cancelled")
            raise

    async def _is_live(self) -> bool:
        """Evaluate the optional liveness gate. Fails OPEN (True) on no check or
        on a check that raises — a broken probe must never cause a false restart."""
        check = self._liveness_check
        if check is None:
            return True
        try:
            result = check()
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception as exc:  # noqa: BLE001 — fail OPEN, never silence the dog
            log.infra.error(
                "[watchdog] _is_live: liveness check raised — failing OPEN (ping)",
                exc_info=exc,
            )
            return True

    @staticmethod
    def _sd_notify(state: str) -> None:
        """Send *state* to systemd via ``systemd-notify``."""
        try:
            result = subprocess.run(
                ["systemd-notify", state],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                log.infra.warning(
                    "[watchdog] _sd_notify: non-zero exit",
                    extra={"_fields": {"returncode": result.returncode, "state": state}},
                )
            else:
                log.infra.debug("[watchdog] _sd_notify: sent WATCHDOG=1")
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.infra.warning("[watchdog] _sd_notify: failed — %s", exc)


class KeepAliveService:
    """Stub for launchd KeepAlive on macOS.

    On macOS, ``KeepAlive=true`` in the plist is handled entirely by launchd;
    no in-process action is required. On all other platforms this class logs
    and returns without starting anything.
    """

    def start(self) -> None:
        """Start keepalive (no-op; managed externally by launchd on macOS)."""
        # 1. ENTRY
        log.infra.debug("[keepalive] start: entry", extra={"_fields": {"platform": sys.platform}})

        # 2. DECISION
        if sys.platform != "darwin":
            log.infra.info("[keepalive] launchd keepalive not configured — skipping")
            return

        # 3. STEP — macOS: launchd owns this; nothing to do in-process
        log.infra.debug("[keepalive] start: decision — macOS detected, launchd manages KeepAlive")

        # 4. EXIT
        log.infra.debug("[keepalive] start: exit — no-op on macOS (launchd owns it)")

    def stop(self) -> None:
        """Stop keepalive (no-op)."""
        log.infra.debug("[keepalive] stop: exit — no-op")
