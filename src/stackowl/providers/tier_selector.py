"""TierSelector — round-robin selection among healthy providers in one tier.

Deliberately SYNC and lock-free: ``ProviderRegistry.get_with_cascade`` (its
only caller) is a sync method used throughout the pipeline, and every
selection here runs to completion without an ``await`` — so under asyncio's
single-threaded event loop, the cursor read-modify-write is inherently
atomic (no preemption mid-bytecode). No new lock is needed; this mirrors the
existing precedent of ``CircuitBreaker.state`` being a cheap sync property
that "tolerates benign staleness" rather than being lock-guarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.providers.circuit_breaker import CircuitState

if TYPE_CHECKING:
    from stackowl.providers.circuit_breaker import CircuitBreaker


class TierSelector:
    """Round-robins across every non-OPEN provider registered for a tier."""

    def __init__(self) -> None:
        self._cursor: dict[str, int] = {}

    def select(
        self,
        tier: str,
        providers: dict[str, object],
        tiers: dict[str, str],
        breakers: dict[str, CircuitBreaker],
    ) -> str | None:
        """Return the next healthy provider NAME for ``tier``, or None if empty/all-OPEN."""
        log.engine.debug("[tier_selector] select: entry", extra={"_fields": {"tier": tier}})
        candidates = [name for name, t in tiers.items() if t == tier and name in providers]
        healthy = [
            name for name in candidates
            if breakers.get(name) is None or breakers[name].state is not CircuitState.OPEN
        ]
        if not healthy:
            log.engine.debug(
                "[tier_selector] select: exit — no healthy provider",
                extra={"_fields": {"tier": tier, "candidates": len(candidates)}},
            )
            return None
        idx = self._cursor.get(tier, 0) % len(healthy)
        chosen = healthy[idx]
        self._cursor[tier] = (idx + 1) % len(healthy)
        log.engine.debug(
            "[tier_selector] select: exit — chosen",
            extra={"_fields": {"tier": tier, "chosen": chosen, "healthy_count": len(healthy)}},
        )
        return chosen
