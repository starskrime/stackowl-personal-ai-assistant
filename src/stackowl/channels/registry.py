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

    _instance: ClassVar[ChannelRegistry | None] = None

    contributor_name: ClassVar[str] = "channel_registry"

    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._source_map: dict[str, list[str]] = {}

    @classmethod
    def instance(cls) -> ChannelRegistry:
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

    def ensure_registered(self, adapter: ChannelAdapter) -> bool:
        """Publish *adapter* unless its channel is already there. True if it registered.

        THE ONE PLACE THAT ANSWERS "make sure this channel resolves", because two
        callers were answering it separately and only one was right.

        `register` RAISES on a duplicate, and warns before it raises — correctly, since
        for its own contract a duplicate is a caller error. But both publishers of a
        socket proxy want something else: *ensure* it is there, where already-present is
        the ordinary case, not a fault. `channels/socket_adapter.py` expressed that by
        asking `get()` first; `startup/orchestrator.py`'s core ingress loop expressed it
        as `contextlib.suppress(Exception)` around the attempt, which is not the same
        thing twice — it is the right answer and the wrong one.

        MEASURED 2026-09-09: 82 `[channel_registry] register: duplicate` warnings, EVERY
        ONE `channel: telegram`, roughly one per boot, in the operator's alarm channel on
        an entirely normal path. The orchestrator's comment claimed the call was
        "idempotent — guarded by `registered`", and that set guards its own call site
        only; the boot-time socket-proxy registration publishes the same names into this
        same singleton first.

        AND THE BROAD SUPPRESS HID MORE THAN THE DUPLICATE. `suppress(Exception)` would
        swallow a genuine registration failure just as quietly, and that failure is
        silent by nature: proactive sends to the channel simply stop resolving. So this
        NEVER RAISES and logs the real failure, which is the no-hidden-errors rule in the
        one place both callers now share.
        """
        name = adapter.channel_name
        try:
            self.get(name)
        except ChannelNotFoundError:
            pass
        else:
            log.gateway.debug(
                "[channel_registry] ensure_registered: already present — no-op",
                extra={"_fields": {"channel": name}},
            )
            return False
        try:
            self.register(adapter)
        except ChannelAlreadyRegisteredError:
            # Raced with another registrar between the ask and the register.
            return False
        except Exception as exc:  # noqa: BLE001 — a publisher must never be blocked
            log.gateway.warning(
                "[channel_registry] ensure_registered: registration FAILED — proactive "
                "sends to this channel will not resolve",
                exc_info=exc,
                extra={"_fields": {"channel": name}},
            )
            return False
        return True

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
