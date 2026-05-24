"""Pipeline services context — ambient service injection via ContextVar."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.messaging.a2a import A2AQueue
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.streaming import StreamRegistry
    from stackowl.providers.registry import ProviderRegistry


@dataclass
class StepServices:
    """Services available to pipeline steps via get_services()."""

    provider_registry: ProviderRegistry | None = field(default=None)
    stream_registry: StreamRegistry | None = field(default=None)
    memory_bridge: MemoryBridge | None = field(default=None)
    owl_registry: OwlRegistry | None = field(default=None)
    a2a_queue: A2AQueue | None = field(default=None)
    kuzu_adapter: KuzuAdapter | None = field(default=None)


_ctx: ContextVar[StepServices] = ContextVar("pipeline_services")


def set_services(services: StepServices) -> Token[StepServices]:
    """Set the pipeline services for the current async context. Returns a reset token."""
    return _ctx.set(services)


def reset_services(token: Token[StepServices]) -> None:
    _ctx.reset(token)


def get_services() -> StepServices:
    """Return the current step services. Returns empty StepServices if not set."""
    try:
        return _ctx.get()
    except LookupError:
        return StepServices()
