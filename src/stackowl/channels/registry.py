"""ChannelRegistry — singleton lookup of all installed channel adapters."""

from __future__ import annotations

from typing import ClassVar

from stackowl.channels.base import ChannelAdapter
from stackowl.exceptions import (
    ChannelAlreadyRegisteredError,
    ChannelNotFoundError,
)
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log


class ChannelRegistry:
    """Singleton registry of channel adapters keyed by ``channel_name``.

    Also satisfies the :class:`HealthContributor` protocol so the platform
    can include "do we have at least one channel attached?" in its overall
    health report.
    """

    _instance: ClassVar["ChannelRegistry | None"] = None

    contributor_name: ClassVar[str] = "channel_registry"

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._source_map: dict[str, list[str]] = {}

    @classmethod
    def instance(cls) -> "ChannelRegistry":
        """Return the process-wide singleton, constructing it lazily."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, adapter: ChannelAdapter, source_name: str | None = None) -> None:
        """Register a channel adapter under its declared name.

        Raises:
            ChannelAlreadyRegisteredError: if the channel name is already taken.
        """
        name = adapter.channel_name
        log.gateway.debug(
            "[channel_registry] register: entry",
            extra={"_fields": {"channel": name, "source": source_name}},
        )
        if name in self._adapters:
            log.gateway.warning(
                "[channel_registry] register: duplicate",
                extra={"_fields": {"channel": name}},
            )
            raise ChannelAlreadyRegisteredError(name)
        log.gateway.debug(
            "[channel_registry] register: decision — accepting new adapter",
            extra={"_fields": {"channel": name, "total_before": len(self._adapters)}},
        )
        self._adapters[name] = adapter
        if source_name:
            self._source_map.setdefault(source_name, []).append(name)
        log.gateway.info(
            "[channel_registry] register: exit",
            extra={"_fields": {"channel": name, "total_after": len(self._adapters)}},
        )

    def unregister_by_source(self, source_name: str) -> int:
        """Remove all adapters registered under source_name. Returns count removed."""
        log.gateway.debug(
            "[channel_registry] unregister_by_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        for name in names:
            self._adapters.pop(name, None)
        log.gateway.debug(
            "[channel_registry] unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    def unregister(self, name: str) -> None:
        """Remove a channel adapter by name.

        Raises:
            ChannelNotFoundError: if no adapter is registered under ``name``.
        """
        log.gateway.debug(
            "[channel_registry] unregister: entry",
            extra={"_fields": {"channel": name}},
        )
        if name not in self._adapters:
            log.gateway.warning(
                "[channel_registry] unregister: not found",
                extra={"_fields": {"channel": name}},
            )
            raise ChannelNotFoundError(name)
        del self._adapters[name]
        log.gateway.info(
            "[channel_registry] unregister: exit",
            extra={"_fields": {"channel": name, "total_after": len(self._adapters)}},
        )

    def get(self, name: str) -> ChannelAdapter:
        """Look up a channel adapter by name.

        Raises:
            ChannelNotFoundError: if no adapter is registered under ``name``.
        """
        if name not in self._adapters:
            raise ChannelNotFoundError(name)
        return self._adapters[name]

    def all(self) -> list[ChannelAdapter]:
        """Return every registered adapter (registration order)."""
        return list(self._adapters.values())

    def reset(self) -> None:
        """Clear every registered adapter — intended for test teardown."""
        log.gateway.debug(
            "[channel_registry] reset: clearing",
            extra={"_fields": {"total": len(self._adapters)}},
        )
        self._adapters.clear()

    async def health_check(self) -> HealthStatus:
        """Report registry health: ok if ≥1 adapter, degraded if zero."""
        count = len(self._adapters)
        if count >= 1:
            return HealthStatus(
                name=self.contributor_name,
                status="ok",
                message=f"{count} channel(s) registered",
                latency_ms=0.0,
            )
        return HealthStatus(
            name=self.contributor_name,
            status="degraded",
            message="no channel adapters registered",
            latency_ms=0.0,
        )
