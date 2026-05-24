"""StartupOrchestrator — 5-phase boot sequence with PID file and dry-run support."""

from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

import platformdirs

from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import default_db_path
from stackowl.exceptions import StartupError
from stackowl.startup.fs_probe import FilesystemProbe
from stackowl.startup.provider_probe import ProviderProbe
from stackowl.startup.watchdog import KeepAlive, WatchdogSec

log = logging.getLogger("stackowl.startup")


def _pid_path() -> Path:
    raw = os.environ.get("STACKOWL_PID_FILE")
    if raw:
        return Path(raw)
    return Path(platformdirs.user_data_dir("stackowl")) / "stackowl.pid"


class StartupOrchestrator:
    """Boots StackOwl through 5 named phases; raises StartupError on any failure."""

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._settings: Settings | None = None

    async def run(self) -> None:
        log.info("[startup] orchestrator.run: entry dry_run=%s", self._dry_run)
        self._settings = Settings()

        phases = [
            (1, "migrations", self._phase_migrations),
            (2, "filesystem", self._phase_filesystem),
            (3, "reconciler", self._phase_reconciler),
            (4, "providers", self._phase_providers),
            (5, "gateway", self._phase_gateway),
        ]
        for num, name, fn in phases:
            t0 = time.monotonic()
            log.info("[startup] phase %d (%s): start", num, name)
            try:
                await fn()
            except StartupError:
                raise
            except Exception as exc:
                log.error("[startup] phase %d (%s): FAILED", num, name, exc_info=exc)
                raise StartupError(num, name, str(exc)) from exc
            log.info("[startup] phase %d (%s): ok (%.0fms)", num, name, (time.monotonic() - t0) * 1000)

        if not self._dry_run:
            self._write_pid()
            WatchdogSec().notify()
            KeepAlive().register()
        log.info("[startup] orchestrator.run: exit — ready")

    async def _phase_migrations(self) -> None:
        if self._dry_run:
            log.info("[startup] reconciler: dry_run — skipping migration application")
            return
        db_path = default_db_path()
        runner = MigrationRunner(db_path=db_path)
        runner.run()

    async def _phase_filesystem(self) -> None:
        FilesystemProbe().check(dry_run=self._dry_run)

    async def _phase_reconciler(self) -> None:
        log.info("[startup] reconciler: ok — no agents to reconcile")

    async def _phase_providers(self) -> None:
        assert self._settings is not None
        providers = self._settings.providers
        if not providers:
            log.info("[startup] providers: no providers configured — skipping probe")
            return
        probe = ProviderProbe(providers)
        await probe.check()

    async def _phase_gateway(self) -> None:
        log.info("[startup] gateway: standby — awaiting Epic 2")
        WatchdogSec().notify()
        KeepAlive().register()

    def _write_pid(self) -> None:
        pid = os.getpid()
        pid_path = _pid_path()
        if pid_path.exists():
            try:
                existing = int(pid_path.read_text(encoding="utf-8").strip())
                log.warning("[startup] WARNING — stale PID file detected (PID %d)", existing)
            except Exception as exc:
                log.warning("[startup] could not read stale PID file: %s", exc)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(pid), encoding="utf-8")
        log.info("[startup] PID %d written to %s", pid, pid_path)
        self._register_pid_cleanup(pid_path)

    def _register_pid_cleanup(self, pid_path: Path) -> None:
        def _cleanup(signum: int, frame: object) -> None:
            pid_path.unlink(missing_ok=True)
            log.info("[startup] PID file removed on signal %d", signum)
            raise SystemExit(0)

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)
