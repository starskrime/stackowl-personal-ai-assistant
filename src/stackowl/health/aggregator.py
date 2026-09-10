"""HealthAggregator — collects health status from all registered contributors."""

from __future__ import annotations

import asyncio
import logging
import time

from stackowl.health.status import HealthContributor, HealthStatus, remedy_for

log = logging.getLogger("stackowl.health")

_CONTRIBUTOR_TIMEOUT = 5.0

#: The window the SECOND probe gets, and why it is wider than the first.
#:
#: MEASURED 2026-09-06. The re-probe exists to tell a SLOW subsystem from a DEAD
#: one — but it was given the identical 5s cap, which asks the same question twice
#: and mostly gets the same answer. Of 63 probes that missed the first window, 48
#: (76%) answered on the re-probe; of the 15 that missed twice and were called
#: ``down``, SIX were reported fully healthy again by the next sweep ~5 minutes
#: later. The other nine were `provider:NeraAiRaw` during real DNS outages, where
#: two strikes worked exactly as intended.
#:
#: The six false ones are load tail-latency, not slow subsystems: `store_cadence`
#: answers in a median of 423ms (p90 1105ms, max 4241ms across 535 successful
#: probes) and was cut off at exactly 5.0s twice; `provider_registry` "answered in
#: ~600ms all day". A second window at 2x the first sits comfortably beyond the
#: slowest successful probe ever recorded, so a merely-loaded box gets to answer
#: while a genuinely dead subsystem still fails and is still called down — the
#: distinction `_TIMEOUT_ATTEMPTS` was built to make, now actually made.
#:
#: The cost is bounded and tiny: `collect()` gathers CONCURRENTLY, so the worst
#: case is one contributor's 5s + 10s rather than a sum, against the scheduler's
#: `_HANDLER_TIMEOUT_SEC` of 1200s.
_CONTRIBUTOR_RETRY_TIMEOUT = _CONTRIBUTOR_TIMEOUT * 2

#: How many timed-out probes it takes to call a subsystem DOWN.
#:
#: MEASURED 2026-08-31: ``provider_registry`` answered in ~600ms all day and
#: exceeded the 5s cap SEVEN times. Each one produced "UNHEALTHY subsystems
#: detected" and a critical operator page, and the next sweep found ok=11/11 and
#: sent the recovery notice — also critical. Seven degraded/recovered pairs in one
#: day, out of 25 critical pages. The provider was never down; the probe was slow.
#:
#: A timeout says something about the PROBE, not about the subsystem, so a single
#: one is not evidence. Damping the alarm would hide a real outage just as well;
#: re-probing tells the two apart, because a genuinely dead subsystem fails the
#: second attempt too — at a cost of one extra probe window on an outage, and
#: nothing at all on the healthy path, which is the one taken almost every time.
_TIMEOUT_ATTEMPTS = 2


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
            return await self._confirm_timeout(contributor, t0)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("[health] aggregator: %s raised: %s", name, exc)
            return HealthStatus(
                name=name, status="down", message=str(exc),
                remedy=remedy_for(exc), latency_ms=latency_ms,
            )

    async def _confirm_timeout(
        self, contributor: HealthContributor, t0: float,
    ) -> HealthStatus:
        """Re-probe a contributor that did not answer, and believe the second word.

        Only a TIMEOUT comes here. An exception is EVIDENCE about the subsystem
        and is never second-guessed; a timeout is a non-answer and says nothing
        yet. See :data:`_TIMEOUT_ATTEMPTS` for the measurement that earned this.

        INFO, not debug, on the first miss: it is the only line that explains why
        a sweep took twice as long, and production runs at INFO.
        """
        name = contributor.contributor_name
        log.info(
            "[health] aggregator: %s did not answer in %.0fs — re-probing with a "
            "%.0fs window before calling it down",
            name, _CONTRIBUTOR_TIMEOUT, _CONTRIBUTOR_RETRY_TIMEOUT,
        )
        # THE RE-PROBE NEEDS ITS OWN CLOCK. `t0` starts at the FIRST probe, which by
        # definition already burned the full first window before we got here, so
        # anything measured from it is always over the threshold and can prove
        # nothing about the second attempt.
        t1 = time.monotonic()
        try:
            status = await asyncio.wait_for(
                contributor.health_check(), timeout=_CONTRIBUTOR_RETRY_TIMEOUT,
            )
        except TimeoutError:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning(
                "[health] aggregator: %s timed out TWICE (%.0fs then %.0fs) after "
                "%.0fms — down",
                name, _CONTRIBUTOR_TIMEOUT, _CONTRIBUTOR_RETRY_TIMEOUT, latency_ms,
            )
            return HealthStatus(
                name=name,
                status="down",
                message=(
                    f"health check timed out twice "
                    f"(>{_CONTRIBUTOR_TIMEOUT:.0f}s, then >{_CONTRIBUTOR_RETRY_TIMEOUT:.0f}s)"
                ),
                # A double timeout is the one `down` with no exception behind it, and
                # it is also the one most likely to be about the HOST rather than the
                # subsystem. Passing the TimeoutError says that out loud instead of
                # leaving a reader to infer an outage from a non-answer.
                remedy=remedy_for(TimeoutError()),
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("[health] aggregator: %s raised on re-probe: %s", name, exc)
            return HealthStatus(
                name=name, status="down", message=str(exc),
                remedy=remedy_for(exc), latency_ms=latency_ms,
            )
        # SAY WHEN THE WIDER WINDOW IS WHAT SAVED IT, and say it as a STRING.
        #
        # DEBT-132 widened the re-probe window from 5s to 10s. Its closing check
        # grepped the line announcing the re-probe — which marks the ATTEMPT, not the
        # OUTCOME. MEASURED 2026-09-07: it reported CLOSEABLE on 14 hits, and every
        # one of those sweeps still ended `ok=14 total=15` because all 14 were a
        # genuinely unreachable provider. The wider window fired 14 times and rescued
        # nothing, while the check said the claim was evidenced.
        #
        # The success line below could not close it either: its wording predates the
        # fix, so a count is satisfied by history, and it carried NO DURATION — so a
        # re-probe answering in 2s (which the old 5s window would also have caught)
        # was indistinguishable from one answering in 8s (which only the wider window
        # can). A fix that moves a THRESHOLD must log the MEASUREMENT the threshold is
        # compared against, or its effect is unfalsifiable.
        #
        # It is a distinct SENTENCE rather than a number to compare, because
        # `scripts/log_since.sh` takes a grep pattern and cannot express `> 5000`.
        # The asymmetry that made this easy to miss: the timeout branch above has
        # logged `latency_ms` all along; only the success branch was silent.
        reprobe_ms = (time.monotonic() - t1) * 1000
        if reprobe_ms > _CONTRIBUTOR_TIMEOUT * 1000:
            log.info(
                "[health] aggregator: %s answered on the re-probe after %.0fms (%s) — "
                "BEYOND the first %.0fs window, so the wider window is what saved it",
                name, reprobe_ms, status.status, _CONTRIBUTOR_TIMEOUT,
            )
        else:
            log.info(
                "[health] aggregator: %s answered on the re-probe after %.0fms (%s) — "
                "a slow probe, not a dead subsystem",
                name, reprobe_ms, status.status,
            )
        return status
