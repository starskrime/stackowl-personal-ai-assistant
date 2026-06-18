"""Per-adapter resolve_target(session_id) (C1 / F104).

The session_id is NOT the send target. Resolution must live IN the adapter that
owns the destination map (asymmetry honored by construction):

* base ChannelAdapter.resolve_target -> None (text-only / single-terminal).
* Telegram -> its numeric private-chat convention (session_id == chat_id).
* Slack -> delegates to its existing target_for_session / _targets map.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from stackowl.channels.base import ChannelAdapter
from stackowl.gateway.scanner import IngressMessage
from stackowl.pipeline.streaming import ResponseChunk


class _BareAdapter(ChannelAdapter):
    """Minimal concrete adapter to exercise the base default."""

    @property
    def channel_name(self) -> str:
        return "bare"

    async def receive(self) -> IngressMessage:  # pragma: no cover — unused
        raise NotImplementedError

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_text(self, text: str) -> None:  # pragma: no cover
        raise NotImplementedError


def test_base_resolve_target_returns_none() -> None:
    assert _BareAdapter().resolve_target("anything") is None


def test_telegram_resolve_target_parses_private_chat_session() -> None:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.telegram.settings import TelegramSettings

    adapter = TelegramChannelAdapter(
        TelegramSettings(bot_token="x", allowed_user_ids=[12345])
    )
    # Telegram private chat: session_id == str(user_id) == chat_id (int).
    assert adapter.resolve_target("12345") == 12345


def test_telegram_resolve_target_non_numeric_is_none() -> None:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.telegram.settings import TelegramSettings

    adapter = TelegramChannelAdapter(
        TelegramSettings(bot_token="x", allowed_user_ids=[12345])
    )
    # A group chat session_id (not the chat id) must NOT be guessed.
    assert adapter.resolve_target("group:abc") is None
    assert adapter.resolve_target("") is None


def test_slack_resolve_target_delegates_to_target_map() -> None:
    from stackowl.channels.slack.adapter import SlackChannelAdapter
    from stackowl.channels.slack.settings import SlackSettings

    adapter = SlackChannelAdapter(
        SlackSettings(bot_token="x", app_token="y", signing_secret="z")
    )
    # Seed the existing session->target map the adapter owns.
    adapter._targets["slack:hashed"] = "C0DEADBEEF"
    assert adapter.resolve_target("slack:hashed") == "C0DEADBEEF"
    assert adapter.resolve_target("slack:unknown") is None
