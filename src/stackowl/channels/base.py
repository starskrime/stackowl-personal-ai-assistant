"""ChannelAdapter ABC — common interface for all I/O channels (Story 9.1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from collections.abc import Callable

from stackowl.gateway.scanner import IngressMessage
from stackowl.health.status import HealthStatus
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

    @property
    @abstractmethod
    def contributor_name(self) -> str:
        """Health-loop contributor name (for healers dict registration)."""
        ...

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
        """Send a plain text message to the user.

        No-target contract (C6 / C-1) — applied identically by every rich
        channel (Telegram, Slack, Discord, WhatsApp) that overrides this with an
        explicit keyword target (``chat_id=`` / ``target=``):

        * An EXPLICIT keyword target was passed (the on-turn path: ``send()``
          captures ``chunk.target`` and forwards it) but it does NOT resolve to a
          live destination ⇒ log at ``error`` and raise
          :class:`~stackowl.exceptions.DeliveryError` — an answer to a turn must
          never be silently dropped (no-hidden-errors).
        * NO explicit target was passed AND the adapter's shared ``_last_*`` is
          ``None`` (the proactive / best-effort path) ⇒ stay a loud ``error``-level
          LOGGED NO-OP, surfaced via the ``DeliveryLedger`` — NEVER a raise, so
          the proactive deliverer's never-raises contract is preserved and no
          false ``failed`` ledger row triggers a retry storm.

        A "fallback chat" is NEVER fabricated (that re-creates the cross-deliver
        bug): the honest behaviour is loud-failure on-turn, visible-status
        best-effort.
        """
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Report the channel's current health status (ADR-6 HealthContributor).

        Used by the health loop to detect degradation and trigger self-healing.
        Every channel must report its liveness and readiness to deliver messages.
        """
        ...

    # ------------------------------------------------------------------ ADR-6 HealableResource protocol

    @property
    @abstractmethod
    def available(self) -> bool:
        """True if the channel is live and ready to send (ADR-6 HealableResource)."""
        ...

    @property
    @abstractmethod
    def unavailable_reason(self) -> str | None:
        """Return the degradation message if unavailable, else None."""
        ...

    @abstractmethod
    async def ensure_available(self) -> None:
        """Recover a degraded channel by restarting its connection if needed."""
        ...

    @abstractmethod
    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """Register a callback to fire when the channel is recycled."""
        ...

    def resolve_target(self, session_id: str) -> str | int | None:
        """Resolve THIS channel's native send destination for ``session_id``.

        The ``session_id`` is NOT itself a send target on every channel (the
        session_id != send-target asymmetry): a Slack ``slack:{hash}`` session
        is not a channel id, while a Telegram private chat's session_id IS the
        numeric chat id. Resolution therefore lives in the adapter that OWNS the
        destination map for its channel.

        Default behaviour: ``None`` — text-only / single-terminal channels (CLI,
        SMS) have no per-session destination, so a proactive send falls back to
        the adapter's shared ``_last_*`` recipient (logged loudly upstream, never
        a silent guess). Channels with a real recipient map (Telegram, Slack)
        override this to return their channel-native token (telegram ``int``
        chat id, slack ``str`` channel id).
        """
        log.gateway.debug(
            "[channel] resolve_target: default None (no per-session destination)",
            extra={"_fields": {"channel": self.channel_name}},
        )
        return None

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

    async def send_clarify(
        self,
        session_id: str,  # noqa: ARG002 — chat-targeting id used only by rich channels
        question: str,
        choices: tuple[str, ...] | list[str],
        clarify_id: str,  # noqa: ARG002 — id carried for rich (button) channels
    ) -> None:
        """Deliver a clarify question to the user (turn-yield model).

        Default behaviour: render a NUMBERED LIST via ``send_text`` — the
        question, then ``1. choice`` / ``2. choice`` / … followed by a prompt to
        reply, or just the question when no choices are given. All channels
        inherit this text-only delivery (``session_id`` is unused here — a CLI
        has one terminal); Telegram overrides this to render tap-buttons whose
        callbacks resolve the parked turn.

        The user's NEXT inbound message on this session+channel resolves the
        clarify (the gateway loop routes it to ``ClarifyGateway.try_resolve``);
        there is no parked coroutine to answer.
        """
        items = [str(c).strip() for c in choices if str(c).strip()]
        if items:
            lines = [question]
            lines.extend(f"{i}. {c}" for i, c in enumerate(items, start=1))
            lines.append("Reply with your choice.")
            text = "\n".join(lines)
        else:
            text = question
        log.gateway.debug(
            "[channel] send_clarify: rendering numbered-list default",
            extra={
                "_fields": {
                    "channel": self.channel_name,
                    "n_choices": len(items),
                }
            },
        )
        await self.send_text(text)

    async def send_file(self, file_path: str, caption: str | None = None) -> None:
        """Send a file/media attachment to the user (E8 send_file).

        Default behaviour: raise :class:`NotImplementedError` — a channel that
        cannot transport binary files (CLI, SMS, a not-yet-implemented adapter)
        signals "file send not supported on <channel>" this way; the
        :class:`ProactiveDeliverer` maps the raise to a structured ``"failed"``
        status and NEVER crashes the turn (self-healing). Channels that can carry
        files (Telegram) override this to upload the bytes with ``caption``.
        """
        raise NotImplementedError(
            f"file send not supported on {self.channel_name!r}"
        )

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
