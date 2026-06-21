"""Reachability census registry — self-registering probes + a fail-closed runner.

A subsystem registers a probe with the :func:`reachability_probe` decorator. The
probe returns a :class:`ProbeResult` saying whether its activation seam is live on
the DEFAULT owl + DEFAULT config. :func:`run_census` runs every registered probe;
:func:`census_passes` is True only if all are reachable (fail-closed: a probe that
raises is recorded as unreachable, never silently skipped).

Drift note (honest): the census fail-closes on every REGISTERED probe and the
:data:`REQUIRED_PROBES` set guards against "added a required subsystem but forgot
its probe". It cannot, by itself, discover a brand-new subsystem that never
registers — that residual gap is the one Dr. Quinn flagged; keep REQUIRED_PROBES
updated when a consequential default-path subsystem is added.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from stackowl.infra.observability import log


@dataclass(frozen=True)
class ProbeResult:
    """One subsystem's reachability verdict on the default path."""

    name: str
    reachable: bool
    detail: str


ProbeFn = Callable[[], Awaitable[ProbeResult]]

_PROBES: dict[str, ProbeFn] = {}

# Consequential default-path subsystems that MUST have a registered probe. Adding a
# new default-path seam? Add its name here AND register its probe (the meta-test
# fails if a required name has no probe). This is the "constitution" list.
REQUIRED_PROBES: frozenset[str] = frozenset({
    "skills.discovery_tools_guaranteed",
    "skills.global_catalog_default_on",
    "telegram.table_formatting",
    "deliver.output_preference_enforcement",
    "budget.counts_tool_calls",
})


def reachability_probe(name: str) -> Callable[[ProbeFn], ProbeFn]:
    """Register ``fn`` as the reachability probe named ``name`` (idempotent)."""
    def _register(fn: ProbeFn) -> ProbeFn:
        _PROBES[name] = fn
        return fn
    return _register


def registered_probes() -> dict[str, ProbeFn]:
    """Return a copy of the probe registry (name → probe)."""
    return dict(_PROBES)


async def run_census() -> list[ProbeResult]:
    """Run every registered probe; a probe that raises → unreachable (fail-closed)."""
    log.gateway.info(
        "[census] run: entry", extra={"_fields": {"probe_count": len(_PROBES)}},
    )
    results: list[ProbeResult] = []
    for name, fn in sorted(_PROBES.items()):
        try:
            results.append(await fn())
        except Exception as exc:  # B5 — a raising probe is a FAILURE, never skipped
            log.gateway.error(
                "[census] probe raised — recording unreachable",
                exc_info=exc, extra={"_fields": {"probe": name}},
            )
            results.append(ProbeResult(name, reachable=False, detail=f"probe raised: {exc}"))
    unreachable = [r.name for r in results if not r.reachable]
    log.gateway.info(
        "[census] run: exit",
        extra={"_fields": {"total": len(results), "unreachable": unreachable}},
    )
    return results


def census_passes(results: list[ProbeResult]) -> bool:
    """True only if every probe is reachable (fail-closed)."""
    return all(r.reachable for r in results)
