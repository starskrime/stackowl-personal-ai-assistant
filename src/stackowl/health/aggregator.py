"""HealthAggregator — collects health status from all registered contributors."""

from __future__ import annotations

import asyncio
import logging
import time

from stackowl.health.status import HealthContributor, HealthStatus

log = logging.getLogger("stackowl.health")

_CONTRIBUTOR_TIMEOUT = 5.0


class HealthAggregator:
    """Collects health status from all registered contributors concurrently."""

    def __init__(self) -> None:
        self._contributors: list[HealthContributor] = []

    def register(self, contributor: HealthContributor) -> None:
        self._contributors.append(contributor)

    async def collect(self) -> list[HealthStatus]:
        log.debug("[health] aggregator.collect: entry — contributors=%d", len(self._contributors))
        tasks = [self._run_contributor(c) for c in self._contributors]
        results = await asyncio.gather(*tasks)
        result_list = list(results)
        ok = sum(1 for r in result_list if r.status == "ok")
        log.info("[health] aggregator.collect: exit — ok=%d total=%d", ok, len(result_list))
        return result_list

    async def is_live(self) -> bool:
        """Liveness verdict for the systemd watchdog gate (F-85).

        Returns ``False`` only when a contributor reports ``"down"`` — a genuinely
        broken critical subsystem (e.g. the DB pool wedged, the data dir
        unwritable). ``"degraded"`` does NOT trip liveness: a degraded subsystem is
        still serving, and killing the process over it would be a false restart.
        With NO contributors registered the process is considered live (fail-open),
        so this is safe to wire before contributors exist."""
        if not self._contributors:
            return True
        statuses = await self.collect()
        down = [s.name for s in statuses if s.status == "down"]
        if down:
            log.warning("[health] aggregator.is_live: DOWN subsystems=%s", down)
            return False
        return True

    async def _run_contributor(self, contributor: HealthContributor) -> HealthStatus:
        name = contributor.contributor_name
        t0 = time.monotonic()
        log.debug("[health] aggregator: probing %s", name)
        try:
            status = await asyncio.wait_for(contributor.health_check(), timeout=_CONTRIBUTOR_TIMEOUT)
            log.debug("[health] aggregator: %s → %s (%.0fms)", name, status.status, status.latency_ms)
            return status
        except TimeoutError:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("[health] aggregator: %s timed out after %.0fms", name, latency_ms)
            return HealthStatus(
                name=name,
                status="down",
                message=f"health check timed out (>{_CONTRIBUTOR_TIMEOUT:.0f}s)",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("[health] aggregator: %s raised: %s", name, exc)
            return HealthStatus(name=name, status="down", message=str(exc), latency_ms=latency_ms)
