"""Built-in health contributors: db, filesystem, provider."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from stackowl.config.provider import ProviderConfig
from stackowl.health.status import HealthStatus

if TYPE_CHECKING:
    from stackowl.channels.liveness import ChannelLivenessStore
    from stackowl.infra.clock import Clock

log = logging.getLogger("stackowl.health")


class GraphContributor:
    """Health contributor for the Kuzu knowledge-graph layer (DUR-5 / F069).

    Reports ``ok`` when the graph adapter initialised, or ``down`` (with the
    init-failure reason) when Kuzu degraded to a None adapter at assembly. This
    makes the memory subsystem's degrade-don't-crash policy operator-visible —
    the asymmetry where Kuzu silently hard-failed startup is replaced by a
    truthful health signal.
    """

    def __init__(self, *, available: bool, reason: str | None = None) -> None:
        self._available = available
        self._reason = reason

    @classmethod
    def probe(cls) -> GraphContributor:
        """Build a contributor by probing whether the Kuzu native layer loads.

        Used by the out-of-process ``health`` CLI command, which must NOT open
        the live graph DB (the serve process holds it). Importing the ``kuzu``
        native module reproduces the exact ARM-wheel-missing failure mode that
        DUR-5 degrades on, so an import failure is reported as ``down`` without
        touching the on-disk database.
        """
        try:
            import kuzu  # noqa: F401
        except Exception as exc:  # pragma: no cover — only on a broken wheel
            return cls(available=False, reason=f"{type(exc).__name__}: {exc}")
        return cls(available=True)

    @property
    def contributor_name(self) -> str:
        return "graph"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.debug("[health] graph_contributor: entry available=%s", self._available)
        latency_ms = (time.monotonic() - t0) * 1000
        if self._available:
            return HealthStatus(
                name="graph", status="ok", message=None, latency_ms=latency_ms
            )
        return HealthStatus(
            name="graph",
            status="down",
            message=self._reason or "knowledge graph unavailable",
            latency_ms=latency_ms,
        )


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


class BrowserContributor:
    """Health contributor: Camoufox runtime status + RSS.

    Reports cold-start time and active-session counts. Does not perform a
    navigation — that would be too expensive for a health probe. Use the
    /browser sessions / settings commands for live drill-down.
    """

    def __init__(self, runtime: object | None, sessions: object | None) -> None:
        self._runtime = runtime
        self._sessions = sessions

    @property
    def contributor_name(self) -> str:
        return "browser"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        runtime = self._runtime
        if runtime is None:
            return HealthStatus(
                name="browser", status="degraded",
                message="runtime not constructed",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        if not getattr(runtime, "available", False):
            reason = getattr(runtime, "unavailable_reason", None) or "unknown"
            return HealthStatus(
                name="browser", status="down",
                message=f"unavailable: {reason}",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        cold = getattr(runtime, "cold_start_ms", None)
        # Best-effort session count (no async call needed — read internal dict).
        session_count = 0
        if self._sessions is not None:
            sessions_dict = getattr(self._sessions, "_sessions", {})
            try:
                session_count = len(sessions_dict)
            except Exception:
                session_count = 0
        rss_mb = _process_rss_mb()
        msg = f"cold_start_ms={int(cold) if cold else '?'} sessions={session_count} rss_mb={rss_mb}"
        return HealthStatus(
            name="browser", status="ok", message=msg,
            latency_ms=(time.monotonic() - t0) * 1000,
        )


def _process_rss_mb() -> int:
    """Best-effort RSS in MB. Returns 0 on platforms without /proc."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) // 1024
    except OSError:
        pass
    return 0


_STALE_AFTER_S = 120.0  # 4 missed 30s heartbeats — matches adapter degrade intent


class ChannelLivenessContributor:
    """Health contributor: is a channel's receive/send path actually alive (RC0)?

    Reads a cross-process ``channel_liveness`` row and turns its AGE into honest
    health. This is the signal that would have caught the 30-hour outage where
    the sweep saw "registered" (in-proc) and reported ok while the real long-poll
    loop was dead in another process.

    Constructed with a ``ChannelLivenessStore`` (any DbPool + Clock) plus the
    channel name to watch. Kept as a SEPARATE contributor rather than folded into
    ``ChannelRegistry.health_check`` on purpose: the registry is a channel-agnostic
    classmethod singleton with no DbPool, and making it telegram-aware + DB-coupled
    would break its single responsibility. Same end result, cleaner seam.

    ``kind`` distinguishes two complementary signals sharing the same
    channel-agnostic table: ``"receive"`` (PB0b — is the inbound poll/long-poll
    loop alive?) and ``"send"`` (PB-CANARY — did a real outbound send recently get
    confirmed delivered?). Defaults to ``"receive"`` and ``stale_after_s`` defaults
    to the original module constant, so PB0b's existing registration call (which
    passes neither) is BYTE-IDENTICAL to before this generalization.
    """

    def __init__(
        self,
        store: ChannelLivenessStore,
        channel: str,
        clock: Clock,
        *,
        kind: Literal["receive", "send"] = "receive",
        stale_after_s: float = _STALE_AFTER_S,
    ) -> None:
        self._store = store
        self._channel = channel
        self._clock = clock
        self._kind = kind
        self._stale_after_s = stale_after_s

    @property
    def contributor_name(self) -> str:
        return f"{self._channel}_{self._kind}"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.debug(
            "[health] channel_liveness_contributor: entry channel=%s kind=%s",
            self._channel, self._kind,
        )
        last = await self._store.read_last_receive_at(self._channel)
        latency_ms = (time.monotonic() - t0) * 1000
        name = f"{self._channel}_{self._kind}"
        if last is None:
            never_msg = (
                f"{self._channel} receive loop never reported alive"
                if self._kind == "receive"
                else f"{self._channel} — no successful send ever confirmed"
            )
            return HealthStatus(
                name=name,
                status="down",
                message=never_msg,
                latency_ms=latency_ms,
            )
        age = (self._clock.now() - last).total_seconds()
        if age > self._stale_after_s:
            stale_msg = (
                f"{self._channel} receive loop stale — last update {int(age)}s ago"
                if self._kind == "receive"
                else f"{self._channel} — no successful send confirmed in the last "
                f"{int(self._stale_after_s)}s (last confirmed {int(age)}s ago)"
            )
            return HealthStatus(
                name=name,
                status="degraded",
                message=stale_msg,
                latency_ms=latency_ms,
            )
        ok_msg = (
            f"last update {int(age)}s ago"
            if self._kind == "receive"
            else f"last send confirmed {int(age)}s ago"
        )
        return HealthStatus(
            name=name,
            status="ok",
            message=ok_msg,
            latency_ms=latency_ms,
        )


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


class ResilienceContributor:
    """Health contributor: per-subsystem recycle counts for HealableResources.

    Reports availability and recycle metadata across all registered resources
    (browser runtime, db pool, providers, memory adapters, etc.) so operators
    can spot flapping subsystems in one place.
    """

    def __init__(self, resources: dict[str, object]) -> None:
        """``resources`` maps a short label ('browser', 'db_pool') to the resource instance."""
        self._resources = resources

    @property
    def contributor_name(self) -> str:
        return "resilience"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.debug("[health] resilience_contributor: entry")
        parts: list[str] = []
        any_unavailable = False
        for label, res in self._resources.items():
            available = bool(getattr(res, "available", True))
            recycle_count = int(getattr(res, "recycle_count", 0))
            reason = getattr(res, "unavailable_reason", None)
            if not available:
                any_unavailable = True
                parts.append(f"{label}:DOWN({reason or 'unknown'})")
            else:
                if recycle_count > 0:
                    parts.append(f"{label}:ok(recycles={recycle_count})")
                else:
                    parts.append(f"{label}:ok")
        latency_ms = (time.monotonic() - t0) * 1000
        return HealthStatus(
            name="resilience",
            status="degraded" if any_unavailable else "ok",
            message=" ".join(parts) if parts else "no healable resources registered",
            latency_ms=latency_ms,
        )
