"""ChannelAdapter ABC — common interface for all I/O channels (Story 9.1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk


class OutboundMessage(BaseModel):
    """Structured outbound message envelope for channels.

    Channels that support rich formatting (Telegram, Slack, Discord) consume
    the ``format`` and ``keyboard`` fields; plain-text channels (CLI, SMS)
    safely ignore everything except ``text``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    format: Literal["plain", "markdown"] = "plain"
    keyboard: dict[str, object] | None = None


class ChannelAdapter(ABC):
    """Abstract I/O channel — CLI, Telegram, Slack, etc. all implement this."""

    @property
    @abstractmethod
    def channel_name(self) -> str: ...

    @abstractmethod
    async def receive(self) -> IngressMessage:
        """Block until the next user message is available."""
        ...

    @abstractmethod
    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        """Stream response chunks to the user."""
        ...

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """Send a plain text message to the user."""
        ...

    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict[str, object],
    ) -> None:
        """Send a message with an inline keyboard attachment.

        Default behaviour: degrade to a plain text send. Channels that support
        inline keyboards (Telegram, Slack) override this to render the buttons.
        """
        log.gateway.debug(
            "[channel] send_inline_keyboard: default fallback to send_text",
            extra={
                "_fields": {
                    "channel": self.channel_name,
                    "keyboard_keys": sorted(keyboard.keys()),
                }
            },
        )
        await self.send_text(text)

    async def download_media(self, file_id: str) -> bytes:
        """Download a media attachment by its channel-specific file ID.

        Default behaviour: raise NotImplementedError. Channels that support
        media (Telegram voice/photo, Slack files) override this.
        """
        raise NotImplementedError(
            f"{self.channel_name!r} does not support media download"
        )

    async def acknowledge_callback(self, callback_id: str, text: str = "") -> None:
        """Acknowledge an inline-keyboard callback query.

        Default behaviour: no-op. Channels that emit inline-callback events
        (Telegram) override this to satisfy their API requirements (e.g.
        Telegram requires answering each callback within 15 seconds).
        """
        log.gateway.debug(
            "[channel] acknowledge_callback: noop — not implemented",
            extra={
                "_fields": {
                    "channel": self.channel_name,
                    "callback_id": callback_id,
                    "text_len": len(text),
                }
            },
        )
