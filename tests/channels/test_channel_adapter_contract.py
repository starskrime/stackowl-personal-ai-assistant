"""Story 9.1 contract tests — OutboundMessage, ChannelRegistry, splitters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator

import pytest
from pydantic import ValidationError

from stackowl.channels.base import ChannelAdapter, OutboundMessage
from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.splitter import (
    DiscordMessageSplitter,
    SlackMessageSplitter,
    TelegramMessageSplitter,
    WhatsAppMessageSplitter,
)
from stackowl.exceptions import (
    ChannelAlreadyRegisteredError,
    ChannelNotFoundError,
)
from stackowl.gateway.scanner import IngressMessage
from stackowl.pipeline.streaming import ResponseChunk


# --- Test helpers ---------------------------------------------------------


class _StubAdapter(ChannelAdapter):
    """Minimal concrete ChannelAdapter for registry / contract tests."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name
        self.sent_text: list[str] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def receive(self) -> IngressMessage:
        return IngressMessage(
            text="hello",
            session_id="s",
            channel=self._name,
            trace_id="t",
        )

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        async for _ in chunks:
            pass

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)


@pytest.fixture()
def clean_registry() -> Generator[ChannelRegistry, None, None]:
    """Yield a freshly-reset singleton registry, reset again on teardown."""
    reg = ChannelRegistry.instance()
    reg.reset()
    try:
        yield reg
    finally:
        reg.reset()


# --- OutboundMessage ------------------------------------------------------


def test_outbound_message_is_frozen() -> None:
    """OutboundMessage must reject attribute mutation and unknown fields."""
    msg = OutboundMessage(text="hello")
    with pytest.raises(ValidationError):
        msg.text = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        OutboundMessage(text="x", unknown_field=1)  # type: ignore[call-arg]


def test_outbound_message_defaults_plain_format() -> None:
    """Default format is plain and keyboard defaults to None."""
    msg = OutboundMessage(text="hi")
    assert msg.format == "plain"
    assert msg.keyboard is None


# --- ChannelRegistry ------------------------------------------------------


def test_channel_registry_singleton() -> None:
    """Two instance() calls must return the same registry object."""
    a = ChannelRegistry.instance()
    b = ChannelRegistry.instance()
    assert a is b


def test_channel_registry_register_and_get(
    clean_registry: ChannelRegistry,
) -> None:
    adapter = _StubAdapter(name="cli")
    clean_registry.register(adapter)
    assert clean_registry.get("cli") is adapter
    assert clean_registry.all() == [adapter]


def test_channel_registry_duplicate_raises(
    clean_registry: ChannelRegistry,
) -> None:
    clean_registry.register(_StubAdapter(name="dup"))
    with pytest.raises(ChannelAlreadyRegisteredError):
        clean_registry.register(_StubAdapter(name="dup"))


def test_channel_registry_not_found_raises(
    clean_registry: ChannelRegistry,
) -> None:
    with pytest.raises(ChannelNotFoundError):
        clean_registry.get("nope")


def test_channel_registry_unregister(
    clean_registry: ChannelRegistry,
) -> None:
    clean_registry.register(_StubAdapter(name="bye"))
    clean_registry.unregister("bye")
    with pytest.raises(ChannelNotFoundError):
        clean_registry.get("bye")
    with pytest.raises(ChannelNotFoundError):
        clean_registry.unregister("bye")


def test_channel_registry_reset(clean_registry: ChannelRegistry) -> None:
    clean_registry.register(_StubAdapter(name="x"))
    clean_registry.register(_StubAdapter(name="y"))
    assert len(clean_registry.all()) == 2
    clean_registry.reset()
    assert clean_registry.all() == []


async def test_channel_registry_health_ok(
    clean_registry: ChannelRegistry,
) -> None:
    clean_registry.register(_StubAdapter(name="cli"))
    status = await clean_registry.health_check()
    assert status.status == "ok"
    assert status.name == "channel_registry"


async def test_channel_registry_health_degraded(
    clean_registry: ChannelRegistry,
) -> None:
    status = await clean_registry.health_check()
    assert status.status == "degraded"


# --- BaseMessageSplitter --------------------------------------------------


def test_base_splitter_no_split_needed() -> None:
    splitter = DiscordMessageSplitter()
    text = "short message"
    assert splitter.split(text) == [text]


def test_base_splitter_paragraph_split() -> None:
    """Long text containing a paragraph break should split at the break."""
    splitter = DiscordMessageSplitter()  # 1900-char limit
    head = "A" * 1500
    tail = "B" * 1500
    text = head + "\n\n" + tail
    chunks = splitter.split(text)
    assert len(chunks) >= 2
    assert chunks[0].endswith("A")
    assert chunks[1].startswith("B")


def test_base_splitter_sentence_split() -> None:
    """Without paragraph breaks, sentence boundaries are preferred."""
    splitter = DiscordMessageSplitter()
    sentence = "X" * 600 + ". "
    text = sentence * 4  # ~2408 chars, no paragraph breaks
    chunks = splitter.split(text)
    assert len(chunks) >= 2
    # First chunk should end on a sentence-terminator after stripping.
    assert chunks[0].rstrip().endswith(".")


def test_base_splitter_hard_split() -> None:
    """When no break exists, splitter falls back to the char_limit edge."""
    splitter = DiscordMessageSplitter()
    text = "C" * 4000
    chunks = splitter.split(text)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= splitter.char_limit


def test_base_splitter_code_fence_not_split() -> None:
    """A triple-backtick fence must never be torn across chunks."""
    splitter = DiscordMessageSplitter()  # 1900-char limit
    prefix = "P" * 1700
    fence_body = "F" * 500
    text = f"{prefix}\n```\n{fence_body}\n```\n"
    chunks = splitter.split(text)
    # The fence must live entirely within one chunk — count fences per chunk.
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0, (
            f"Chunk contains odd number of fences: {chunk[:80]!r}"
        )


# --- Per-channel limits ---------------------------------------------------


def test_telegram_splitter_limit() -> None:
    assert TelegramMessageSplitter().char_limit == 3800


def test_discord_splitter_limit() -> None:
    assert DiscordMessageSplitter().char_limit == 1900


def test_slack_splitter_limit() -> None:
    assert SlackMessageSplitter().char_limit == 3900


def test_whatsapp_splitter_limit() -> None:
    assert WhatsAppMessageSplitter().char_limit == 4000
