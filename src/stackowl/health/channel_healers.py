"""ChannelHealers — find a channel's self-heal actuator when the sweep looks for it.

WHY THIS EXISTS. Telegram had health DETECTION and no ACTUATOR, and had since the
wiring was written. Every boot logged "[scheduler] assembly: telegram healer setup
failed — health detection via ChannelLivenessContributor only", which reads like a
graceful degrade and was actually a permanently open ADR-6 loop on the primary
channel. Two independent defects, either fatal on its own:

1. ORDERING. The wiring called ``ChannelRegistry.get("telegram")`` and tested for
   ``None`` — but ``get()`` never returns ``None``, it RAISES. So the success
   branch and its ``else`` were dead code, and the miss landed in the ``except``.
   It missed on EVERY boot because scheduler assembly runs before the adapters
   start: measured, assembly exits 03:31:19 and Telegram starts 03:31:45.

2. KEY MISMATCH. The sweep does ``healers.get(status.name)`` — a plain exact
   string match. The healer would have been keyed ``"telegram"``, while the
   statuses that report the channel unhealthy are ``"telegram_receive"`` and
   ``"telegram_canary_send"``: ChannelLivenessContributor names itself
   ``f"{channel}_{kind}"``.

RESOLUTION HAPPENS AT LOOKUP TIME, which removes the ordering dependency instead
of moving it. An eager snapshot is correct only when taken after every adapter has
started, and "call these in the right order" is the constraint that silently
regressed here. A lookup cannot be too early.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.infra.resilience import HealableResource

#: Suffixes ChannelLivenessContributor appends to the channel it watches — its
#: ``kind`` is Literal["receive", "send"], so these are the whole set. Stripping
#: one is the inverse of the name it builds.
_KIND_SUFFIXES = ("_receive", "_send")

#: Status names whose channel is not the channel that can fix them. The canary is
#: a SEND-path signal FOR telegram, not a channel of its own — and a dead send
#: path is exactly when recycling the adapter is the right response.
_STATUS_CHANNEL_ALIASES = {"telegram_canary": "telegram"}


def _channel_for_status(status_name: str) -> str:
    """"telegram_receive" -> "telegram"; "telegram_canary_send" -> "telegram"."""
    base = status_name
    for suffix in _KIND_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return _STATUS_CHANNEL_ALIASES.get(base, base)


class ChannelHealers(Mapping[str, "HealableResource"]):
    """The static healer map, plus channel adapters resolved on demand.

    Wraps rather than replaces: ``db``, the embedding registry and the providers
    are still passed in as concrete entries and are returned unchanged.
    """

    def __init__(self, static: Mapping[str, HealableResource]) -> None:
        self._static = dict(static)

    def get(  # type: ignore[override]
        self, key: str, default: HealableResource | None = None
    ) -> HealableResource | None:
        found = self._static.get(key)
        if found is not None:
            return found
        return self._resolve_channel(key) or default

    def __getitem__(self, key: str) -> HealableResource:
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def __iter__(self) -> Iterator[str]:
        # Only the static keys are enumerable. The channel entries exist as
        # answers to a lookup, not as a list — enumerating them would mean
        # deciding which of a registry's adapters "count" before anyone asked.
        return iter(self._static)

    def __len__(self) -> int:
        return len(self._static)

    def __bool__(self) -> bool:
        """Truthy when something COULD be healed, not when the static dict is full.

        health_sweep guards its heal loop with ``if not self._healers``. Falling
        back to ``len(self._static)`` would mean a deployment whose only healable
        subsystems are channels never enters the loop at all — reintroducing the
        same class of bug this class exists to fix.
        """
        if self._static:
            return True
        return any(self._healable(a) for a in self._registered_adapters())

    # ----- internal ---------------------------------------------------------

    @staticmethod
    def _registered_adapters() -> list[object]:
        try:
            from stackowl.channels.registry import ChannelRegistry

            return list(ChannelRegistry.instance().all())
        except Exception:  # pragma: no cover — a registry failure is not a heal failure
            return []

    @staticmethod
    def _healable(adapter: object) -> bool:
        """A channel adapter is only a healer if it can actually be recycled.

        Returning one without ``ensure_available`` would turn the sweep's heal
        step into an AttributeError inside its own try block — a heal that
        reports failure for a reason unrelated to the subsystem's health.
        """
        return callable(getattr(adapter, "ensure_available", None))

    def _resolve_channel(self, status_name: str) -> HealableResource | None:
        channel = _channel_for_status(status_name)
        try:
            from stackowl.channels.registry import ChannelRegistry

            adapter = ChannelRegistry.instance().get(channel)
        except Exception:
            # Not registered (the adapter has not started, or the channel is not
            # configured). ChannelRegistry.get RAISES rather than returning None,
            # and this is the exact miss that used to be logged as a failure.
            return None
        if not self._healable(adapter):
            log.scheduler.debug(
                "[health] channel_healers: adapter is registered but not healable",
                extra={"_fields": {"status": status_name, "channel": channel}},
            )
            return None
        log.scheduler.debug(
            "[health] channel_healers: resolved a channel healer",
            extra={"_fields": {"status": status_name, "channel": channel}},
        )
        return adapter  # type: ignore[return-value]


__all__ = ["ChannelHealers"]
