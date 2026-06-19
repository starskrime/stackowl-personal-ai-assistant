"""UrgentCommand — ``/urgent <message>`` slash command (Story 7.4).

Broadcasts a critical notification to every known channel.  Because the
:class:`NotificationRouter` treats ``urgency="critical"`` as an unconditional
deliver, every channel sees the message immediately regardless of focus mode
or quiet-hours configuration.

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
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log
from stackowl.notifications.router import Notification, NotificationRouter

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.state import PipelineState


_CATEGORY = "user_urgent"
_FALLBACK_CHANNELS = ["cli"]


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
    ) -> None:
        self._router: NotificationRouter = router  # type: ignore[assignment]  # guarded in handle()
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

    async def handle(self, args: str, state: PipelineState) -> str:
        channels = self._resolve_channels()
        log.notifications.debug(
            "[notifications] urgent.handle: entry",
            extra={
                "_fields": {
                    "args_len": len(args),
                    "session": state.session_id,
                    "channel_count": len(channels),
                }
            },
        )
        if self._router is None:
            return "✗ /urgent: not configured"
        message = args.strip()
        if not message:
            log.notifications.debug("[notifications] urgent.handle: empty message")
            return "urgent: message required"

        log.notifications.debug(
            "[notifications] urgent.handle: dispatching to channels",
            extra={"_fields": {"channels": channels}},
        )
        tasks = [
            self._router.deliver(
                Notification(
                    message=message,
                    urgency="critical",
                    category=_CATEGORY,
                    channel_name=ch,
                )
            )
            for ch in channels
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        delivered = 0
        failed = 0
        for res in results:
            if isinstance(res, BaseException):
                failed += 1
                log.notifications.warning(
                    "[notifications] urgent.handle: delivery raised",
                    exc_info=res,
                )
            else:
                delivered += 1

        log.notifications.info(
            "[notifications] urgent.handle: exit",
            extra={"_fields": {"delivered": delivered, "failed": failed}},
        )
        if failed:
            return f"urgent: broadcast to {delivered}/{len(channels)} channels ({failed} failed)"
        return f"urgent: broadcast to {delivered} channels"

    @classmethod
    def create_and_register(
        cls,
        router: NotificationRouter,
        channels: list[str] | None = None,
    ) -> UrgentCommand:
        cmd = cls(router=router, channels=channels)
        CommandRegistry.instance().register(cmd)
        return cmd
