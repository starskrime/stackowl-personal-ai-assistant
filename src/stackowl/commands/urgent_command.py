"""UrgentCommand — ``/urgent <message>`` slash command (Story 7.4).

Broadcasts a critical notification to every known channel.  Because the
:class:`NotificationRouter` treats ``urgency="critical"`` as an unconditional
deliver, every channel sees the message immediately regardless of focus mode
or quiet-hours configuration.

TRANSPORT (F-76 — the honest contract). The :class:`NotificationRouter` is a
pure routing/audit component that "never touches a channel adapter" — it only
decides ``delivered``/``batched``/``suppressed`` and writes an audit row.
Counting that DECISION as a "broadcast" was an overclaim: nothing reached the
user.  ``/urgent`` therefore transports through the :class:`ProactiveDeliverer`
(the seam that actually calls ``send_text`` and returns a real
:data:`DeliveryStatus`) and derives its user-facing count from the ACTUAL
``delivered`` outcomes, never from the mere absence of an exception.  When no
deliverer is wired the command degrades HONESTLY — it reports only that the
message was *routed* (a decision), and flags that transport is not wired —
rather than claiming a delivery that did not occur.

Channel roster is derived from the live :class:`ChannelRegistry` at dispatch
time so that any channel registered after boot (Telegram, Slack, WhatsApp …)
is automatically included.  Falls back to ``["cli"]`` only when the registry
is completely empty.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log
from stackowl.notifications.router import Notification, NotificationRouter

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.pipeline.state import PipelineState


_CATEGORY = "user_urgent"
_FALLBACK_CHANNELS = ["cli"]

_URGENT_META = CommandMeta(
    grammar="flag",
    group="Notifications",
    args=(Arg("message", summary="the urgent message to broadcast"),),
)


class UrgentCommand(SlashCommand):
    """Broadcast a critical notification to all registered channels.

    The channel roster is resolved from :class:`ChannelRegistry` at dispatch
    time, so channels registered after startup are included automatically.
    A fixed override list can be injected via the *channels* parameter (for
    tests or explicit scoping); pass an empty list to use the live registry.
    """

    def __init__(
        self,
        router: NotificationRouter | None = None,
        channels: list[str] | None = None,
        deliverer: ProactiveDeliverer | None = None,
    ) -> None:
        self._router: NotificationRouter = router  # type: ignore[assignment]  # guarded in handle()
        # The REAL transport seam (F-76). When wired, /urgent routes AND
        # transports through it and counts only genuine ``delivered`` outcomes.
        # When None it degrades to a router-only routing decision, reported
        # honestly as "routed" (not "delivered").
        self._deliverer = deliverer
        # None  → derive from live ChannelRegistry at dispatch time (production)
        # list  → use exactly this roster (tests / explicit override)
        self._override_channels: list[str] | None = list(channels) if channels is not None else None

    def _resolve_channels(self) -> list[str]:
        """Return the channel roster for this broadcast.

        Production path: pull names from the live :class:`ChannelRegistry`,
        falling back to ``["cli"]`` if nothing is registered yet.
        Override path: return the list injected at construction time (tests).
        """
        if self._override_channels is not None:
            return list(self._override_channels)
        adapters = ChannelRegistry.instance().all()
        if adapters:
            names = [a.channel_name for a in adapters]
            log.notifications.debug(
                "[notifications] urgent._resolve_channels: live registry",
                extra={"_fields": {"channels": names}},
            )
            return names
        log.notifications.debug(
            "[notifications] urgent._resolve_channels: registry empty — using fallback",
            extra={"_fields": {"fallback": _FALLBACK_CHANNELS}},
        )
        return list(_FALLBACK_CHANNELS)

    @property
    def command(self) -> str:
        return "urgent"

    @property
    def description(self) -> str:
        return "Broadcast a critical notification to all registered channels."

    @property
    def meta(self) -> CommandMeta:
        return _URGENT_META

    async def handle(self, args: str, state: PipelineState) -> str:
        channels = self._resolve_channels()
        log.notifications.debug(
            "[notifications] urgent.handle: entry",
            extra={
                "_fields": {
                    "args_len": len(args),
                    "session": state.session_id,
                    "channel_count": len(channels),
                    "has_deliverer": self._deliverer is not None,
                }
            },
        )
        # Neither a transport seam NOR a router means nothing can happen — honest.
        if self._deliverer is None and self._router is None:
            return "✗ /urgent: not configured"
        message = args.strip()
        if not message:
            log.notifications.debug("[notifications] urgent.handle: empty message")
            return "urgent: message required\n" + render_usage("urgent", _URGENT_META)

        notifications = [
            Notification(
                message=message,
                urgency="critical",
                category=_CATEGORY,
                channel_name=ch,
            )
            for ch in channels
        ]

        # PREFERRED PATH (F-76) — transport through the real deliverer seam and
        # count ONLY genuine ``delivered`` outcomes (``failed`` does not count).
        if self._deliverer is not None:
            return await self._broadcast_via_deliverer(notifications, channels)

        # DEGRADED PATH — no transport seam wired. We can only make a routing
        # decision; report it HONESTLY as "routed" (a decision), never as a
        # delivery, and flag that transport is not wired so the claim is true.
        return await self._route_only(notifications, channels)

    async def _broadcast_via_deliverer(
        self, notifications: list[Notification], channels: list[str]
    ) -> str:
        """Transport each notification and count real ``delivered`` outcomes."""
        log.notifications.debug(
            "[notifications] urgent.handle: transporting via deliverer",
            extra={"_fields": {"channels": channels}},
        )
        assert self._deliverer is not None  # narrowed by caller
        tasks = [self._deliverer.deliver(n) for n in notifications]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        delivered = 0
        failed = 0
        for res in results:
            # ``delivered`` is the ONLY outcome that proves transport reached the
            # user. ``failed``/``batched``/``suppressed`` and any raised exception
            # are NOT a delivery and must never inflate the count.
            if isinstance(res, BaseException):
                failed += 1
                log.notifications.warning(
                    "[notifications] urgent.handle: transport raised",
                    exc_info=res,
                )
            elif res == "delivered":
                delivered += 1
            else:
                failed += 1
                log.notifications.warning(
                    "[notifications] urgent.handle: not delivered",
                    extra={"_fields": {"status": str(res)}},
                )

        total = len(channels)
        log.notifications.info(
            "[notifications] urgent.handle: exit",
            extra={"_fields": {"delivered": delivered, "failed": failed}},
        )
        if delivered == total:
            return f"urgent: delivered to {delivered} channels"
        return f"urgent: delivered to {delivered}/{total} channels ({failed} failed)"

    async def _route_only(
        self, notifications: list[Notification], channels: list[str]
    ) -> str:
        """No deliverer wired — only a routing decision is possible (honest)."""
        log.notifications.warning(
            "[notifications] urgent.handle: no transport seam — routing only",
            extra={"_fields": {"channels": channels}},
        )
        tasks = [self._router.deliver(n) for n in notifications]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        routed = sum(1 for res in results if not isinstance(res, BaseException))
        failed = len(results) - routed
        for res in results:
            if isinstance(res, BaseException):
                log.notifications.warning(
                    "[notifications] urgent.handle: routing raised",
                    exc_info=res,
                )
        log.notifications.info(
            "[notifications] urgent.handle: exit (route-only)",
            extra={"_fields": {"routed": routed, "failed": failed}},
        )
        suffix = " (transport not wired — not yet delivered)"
        if failed:
            return f"urgent: routed to {routed}/{len(channels)} channels ({failed} failed){suffix}"
        return f"urgent: routed to {routed} channels{suffix}"

    @classmethod
    def create_and_register(
        cls,
        router: NotificationRouter,
        channels: list[str] | None = None,
        deliverer: ProactiveDeliverer | None = None,
    ) -> UrgentCommand:
        cmd = cls(router=router, channels=channels, deliverer=deliverer)
        CommandRegistry.instance().register(cmd)
        return cmd
