"""UrgentCommand — ``/urgent <message>`` slash command (Story 7.4).

Broadcasts a critical notification to every known channel.  Because the
:class:`NotificationRouter` treats ``urgency="critical"`` as an unconditional
deliver, every channel sees the message immediately regardless of focus mode
or quiet-hours configuration.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log
from stackowl.notifications.router import Notification, NotificationRouter

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.state import PipelineState


_CATEGORY = "user_urgent"


class UrgentCommand(SlashCommand):
    """Broadcast a critical notification to all registered channels."""

    def __init__(
        self,
        router: NotificationRouter,
        channels: list[str] | None = None,
    ) -> None:
        self._router = router
        self._channels: list[str] = list(channels) if channels else ["cli"]

    @property
    def command(self) -> str:
        return "urgent"

    @property
    def description(self) -> str:
        return "Broadcast a critical notification to all channels."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.notifications.debug(
            "[notifications] urgent.handle: entry",
            extra={
                "_fields": {
                    "args_len": len(args),
                    "session": state.session_id,
                    "channel_count": len(self._channels),
                }
            },
        )
        message = args.strip()
        if not message:
            log.notifications.debug("[notifications] urgent.handle: empty message")
            return "urgent: message required"

        log.notifications.debug(
            "[notifications] urgent.handle: dispatching to channels",
            extra={"_fields": {"channels": list(self._channels)}},
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
            for ch in self._channels
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
            return f"urgent: broadcast to {delivered}/{len(self._channels)} channels ({failed} failed)"
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
