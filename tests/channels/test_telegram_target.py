"""Task 4 — per-message Telegram target (no cross-deliver under concurrency).

Under concurrency, ``send_text`` historically targeted the shared mutable
``self._last_chat_id`` (overwritten on every inbound update), so a turn finishing
after a newer inbound update could deliver to the WRONG chat. These tests pin the
fix: an explicit per-message ``chat_id`` wins; an omitted ``chat_id`` falls back to
``_last_chat_id`` for back-compat callers; and the streaming ``send`` path resolves
each turn's target from ``chunk.target`` so two sessions never cross-deliver.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import IngressMessage
from stackowl.pipeline.streaming import ResponseChunk


def test_ingress_message_carries_optional_chat_id() -> None:
    m = IngressMessage(
        text="hi", session_id="s1", channel="telegram", trace_id="req-1", chat_id=999
    )
    assert m.chat_id == 999
    m2 = IngressMessage(text="hi", session_id="s1", channel="cli", trace_id="req-2")
    assert m2.chat_id is None


class _FakeBot:
    """Records (chat_id, text) per outbound send_message call."""

    def __init__(self, sent: list[tuple[int, str]]) -> None:
        self.bot = self
        self._sent = sent

    async def send_message(self, *, chat_id: int, text: str, **_: object) -> None:
        self._sent.append((chat_id, text))


def _make_adapter(sent: list[tuple[int, str]]) -> TelegramChannelAdapter:
    """Build a TelegramChannelAdapter with a fake bot + a stub splitter (1 part)."""
    adapter = TelegramChannelAdapter.__new__(TelegramChannelAdapter)
    adapter._bot_app = _FakeBot(sent)  # type: ignore[attr-defined]
    adapter._last_chat_id = 111  # type: ignore[attr-defined]
    adapter._flood_until = None  # type: ignore[attr-defined]

    class _NoSplit:
        def split(self, text: str) -> list[str]:
            return [text]

    class _IdentityFormatter:
        def format_response(self, text: str) -> str:
            return text

    adapter._splitter = _NoSplit()  # type: ignore[attr-defined]
    adapter._formatter = _IdentityFormatter()  # type: ignore[attr-defined]
    return adapter


async def test_send_text_targets_explicit_chat_id_not_last() -> None:
    sent: list[tuple[int, str]] = []
    adapter = _make_adapter(sent)
    TestModeGuard.deactivate()
    try:
        await adapter.send_text("to-A", chat_id=222)
        await adapter.send_text("to-default")  # falls back to _last_chat_id
    finally:
        TestModeGuard.activate()
    assert (222, "to-A") in sent
    assert (111, "to-default") in sent


async def test_two_sessions_do_not_cross_deliver() -> None:
    """Two telegram turns (different chat_id) each deliver to their OWN chat.

    Even though ``_last_chat_id`` is left pointing at chat B (the most-recent
    inbound update), the turn whose chunks carry ``target=A`` must still land in
    chat A. This is the response-side mirror of the cross-deliver bug.
    """
    sent: list[tuple[int, str]] = []
    adapter = _make_adapter(sent)
    # _last_chat_id is the LATEST inbound chat (B) — the cross-deliver trap.
    adapter._last_chat_id = 222  # type: ignore[attr-defined]

    async def _stream(text: str, target: int) -> AsyncIterator[ResponseChunk]:
        yield ResponseChunk(
            content=text,
            is_final=True,
            chunk_index=0,
            trace_id="req",
            owl_name="owl",
            target=target,
        )

    TestModeGuard.deactivate()
    try:
        # Turn for chat A (333) finishes while _last_chat_id points at B (222).
        await adapter.send(_stream("answer-for-A", target=333))
        # Turn for chat B (222) carries its own target explicitly too.
        await adapter.send(_stream("answer-for-B", target=222))
    finally:
        TestModeGuard.activate()

    assert (333, "answer-for-A") in sent, f"A cross-delivered: {sent}"
    assert (222, "answer-for-B") in sent, f"B mis-delivered: {sent}"
    # Crucially, A's answer must NOT have gone to _last_chat_id (222).
    assert (222, "answer-for-A") not in sent
