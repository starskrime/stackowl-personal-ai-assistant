"""Evolution job handler — registers EvolutionCoordinator with HandlerRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool
    from stackowl.owls.concurrency import ConcurrencyGovernor
    from stackowl.owls.registry import OwlRegistry
    from stackowl.providers.registry import ProviderRegistry


def register_evolution_handler(
    db: DbPool,
    provider_registry: ProviderRegistry,
    owl_registry: OwlRegistry,
    evolution_batch_size: int = 10,
    delegation_governor: ConcurrencyGovernor | None = None,
) -> None:
    """Construct and register the ``EvolutionCoordinator`` job handler.

    Called from the startup orchestrator once the DB pool, provider
    registry, and owl registry have all been built. Kept as a thin factory
    so the heavy ``evolution`` module is only imported when the scheduler
    is actually being wired up.
    """
    log.heartbeat.debug(
        "[scheduler] evolution handler: register entry",
        extra={"_fields": {"batch_size": evolution_batch_size}},
    )
    from stackowl.owls.evolution import EvolutionCoordinator

    handler = EvolutionCoordinator(
        db,
        provider_registry,
        owl_registry,
        evolution_batch_size=evolution_batch_size,
        delegation_governor=delegation_governor,
    )
    HandlerRegistry.instance().register(handler)
    log.heartbeat.info(
        "[scheduler] evolution handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
