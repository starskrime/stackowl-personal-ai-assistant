"""Backend factory — selects the pipeline orchestration backend by name.

Resolves the configured ``OrchestratorSettings.backend`` to a concrete
:class:`OrchestratorBackend`. ``"asyncio"`` is the known-good default; unknown
names degrade (fail-safe) to AsyncioBackend rather than raising, so a bad config
value never bricks startup.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
from stackowl.pipeline.services import StepServices


def create_backend(name: str, *, services: StepServices) -> OrchestratorBackend:
    """Construct the pipeline backend named ``name``.

    ``"langgraph"`` returns a :class:`LangGraphBackend`, ``"asyncio"`` returns an
    :class:`AsyncioBackend`. Any other value logs a warning and falls back to
    AsyncioBackend (the known-good backend) — this path degrades, never raises.
    """
    log.engine.info(
        "[backend_factory] create_backend: entry",
        extra={"_fields": {"requested": name}},
    )

    backend: OrchestratorBackend
    if name == "langgraph":
        backend = LangGraphBackend(services=services)
        chosen = "langgraph"
    elif name == "asyncio":
        backend = AsyncioBackend(services=services)
        chosen = "asyncio"
    else:
        log.engine.warning(
            "[backend_factory] create_backend: unknown backend — falling back to asyncio",
            extra={"_fields": {"requested": name, "fallback": "asyncio"}},
        )
        backend = AsyncioBackend(services=services)
        chosen = "asyncio"

    log.engine.info(
        "[backend_factory] create_backend: exit",
        extra={"_fields": {"requested": name, "chosen": chosen}},
    )
    return backend
