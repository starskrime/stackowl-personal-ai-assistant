"""channels package — adapters, registry, and message splitters."""

from stackowl.channels.base import ChannelAdapter, OutboundMessage
from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.splitter import (
    BaseMessageSplitter,
    DiscordMessageSplitter,
    SlackMessageSplitter,
    TelegramMessageSplitter,
    WhatsAppMessageSplitter,
)

__all__ = [
    "BaseMessageSplitter",
    "ChannelAdapter",
    "ChannelRegistry",
    "DiscordMessageSplitter",
    "OutboundMessage",
    "SlackMessageSplitter",
    "TelegramMessageSplitter",
    "WhatsAppMessageSplitter",
]
