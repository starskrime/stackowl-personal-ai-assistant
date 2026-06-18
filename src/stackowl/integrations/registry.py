"""IntegrationRegistry — singleton registry for IntegrationAdapter instances."""
from __future__ import annotations

import logging
from typing import ClassVar, TYPE_CHECKING

from stackowl.exceptions import IntegrationNotFoundError

if TYPE_CHECKING:
    from stackowl.integrations.base import IntegrationAdapter

log = logging.getLogger("stackowl.integrations")


class IntegrationRegistry:
    """Singleton registry of IntegrationAdapter instances.

    Open for extension: Epic 10 plugins can call register() at import time.
    """

    _instance: ClassVar[IntegrationRegistry | None] = None

    def __init__(self) -> None:
        self._adapters: dict[str, IntegrationAdapter] = {}

    @classmethod
    def instance(cls) -> IntegrationRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — test use only."""
        cls._instance = None

    def register(self, adapter: IntegrationAdapter) -> None:
        log.debug("integrations.registry.register: entry", extra={"_fields": {"service": adapter.service_name}})
        self._adapters[adapter.service_name] = adapter
        log.debug("integrations.registry.register: exit")

    def get(self, service_name: str) -> IntegrationAdapter:
        log.debug("integrations.registry.get: entry", extra={"_fields": {"service": service_name}})
        adapter = self._adapters.get(service_name)
        if adapter is None:
            raise IntegrationNotFoundError(service_name)
        log.debug("integrations.registry.get: exit")
        return adapter

    async def list_connected(self) -> list[IntegrationAdapter]:
        log.debug("integrations.registry.list_connected: entry")
        result = []
        for adapter in self._adapters.values():
            try:
                if await adapter.is_connected():
                    result.append(adapter)
            except Exception as exc:
                log.warning(
                    "integrations.registry.list_connected: adapter check failed",
                    exc_info=exc,
                    extra={"_fields": {"service": adapter.service_name}},
                )
        log.debug("integrations.registry.list_connected: exit", extra={"_fields": {"count": len(result)}})
        return result

    def list_all(self) -> list[IntegrationAdapter]:
        log.debug("integrations.registry.list_all: entry")
        result = list(self._adapters.values())
        log.debug("integrations.registry.list_all: exit", extra={"_fields": {"count": len(result)}})
        return result
