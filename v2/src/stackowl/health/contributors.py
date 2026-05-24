"""Built-in health contributors: db, filesystem, provider."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from stackowl.config.provider import ProviderConfig
from stackowl.health.status import HealthStatus

log = logging.getLogger("stackowl.health")


class DbContributor:
    """Health contributor: SQLite database reachability."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @property
    def contributor_name(self) -> str:
        return "db"

    async def health_check(self) -> HealthStatus:
        import asyncio

        log.debug("[health] db_contributor: entry")
        t0 = time.monotonic()

        def _ping() -> None:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()

        if not self._db_path.exists():
            return HealthStatus(
                name="db",
                status="down",
                message=f"database not found: {self._db_path}",
                latency_ms=0.0,
            )
        try:
            await asyncio.to_thread(_ping)
            latency_ms = (time.monotonic() - t0) * 1000
            log.debug("[health] db_contributor: exit — ok (%.0fms)", latency_ms)
            return HealthStatus(name="db", status="ok", message=None, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("[health] db_contributor: ping failed: %s", exc)
            return HealthStatus(name="db", status="down", message=str(exc), latency_ms=latency_ms)


class FilesystemContributor:
    """Health contributor: data and log directory writability."""

    def __init__(self, data_dir: Path, log_dir: Path) -> None:
        self._data_dir = data_dir
        self._log_dir = log_dir

    @property
    def contributor_name(self) -> str:
        return "filesystem"

    async def health_check(self) -> HealthStatus:
        log.debug("[health] fs_contributor: entry")
        t0 = time.monotonic()
        for label, path in [("data_dir", self._data_dir), ("log_dir", self._log_dir)]:
            if not path.exists():
                return HealthStatus(
                    name="filesystem",
                    status="down",
                    message=f"{label} missing: {path}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
        latency_ms = (time.monotonic() - t0) * 1000
        log.debug("[health] fs_contributor: exit — ok (%.0fms)", latency_ms)
        return HealthStatus(name="filesystem", status="ok", message=None, latency_ms=latency_ms)


class ProviderContributor:
    """Health contributor: provider HTTP connectivity."""

    def __init__(self, provider: ProviderConfig) -> None:
        self._provider = provider

    @property
    def contributor_name(self) -> str:
        return f"provider:{self._provider.name}"

    async def health_check(self) -> HealthStatus:
        from stackowl.startup.provider_probe import probe_provider

        log.debug("[health] provider_contributor: entry name=%s", self._provider.name)
        result = await probe_provider(self._provider)
        status = "ok" if result.status == "ok" else "degraded"
        return HealthStatus(
            name=f"provider:{result.name}",
            status=status,  # type: ignore[arg-type]
            message=result.reason,
            latency_ms=result.latency_ms,
        )
