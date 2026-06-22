"""adapter.send() — progress chunks drive a live status; answer stays clean.

Critical regression guards:
  * a progress chunk NEVER lands in the answer body (no buffer contamination);
  * with NO progress chunks the path is byte-identical to the prior behaviour.
"""

from __future__ import annotations

import types
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.progress_settings import ProgressSettings
from stackowl.pipeline.streaming import ResponseChunk


def _settings() -> TelegramSettings:
    return TelegramSettings(bot_token="x", allowed_user_ids=[1])


def _adapter(**progress: float) -> tuple[TelegramChannelAdapter, MagicMock]:
    # flicker_guard_ms=0 so the test's near-instant progress actually sends a
    # status (the guard would otherwise suppress sub-400ms progress).
    ps = ProgressSettings(flicker_guard_ms=0.0, **progress)  # type: ignore[arg-type]
    adapter = TelegramChannelAdapter(_settings(), progress=ps)
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=types.SimpleNamespace(message_id=777))
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


async def _stream(*chunks: ResponseChunk) -> AsyncIterator[ResponseChunk]:
    for c in chunks:
        yield c


def _p(text: str, target: int = 555) -> ResponseChunk:
    return ResponseChunk(content=text, is_final=False, chunk_index=-2,
                         trace_id="t", owl_name="o", target=target, kind="progress")


def _a(text: str, target: int = 555) -> ResponseChunk:
    return ResponseChunk(content=text, is_final=False, chunk_index=0,
                         trace_id="t", owl_name="o", target=target, kind="answer")


@pytest.mark.asyncio
async def test_progress_chunks_excluded_from_answer_body() -> None:
    adapter, bot = _adapter()
    await adapter.send(_stream(
        _p("🔎 Searching the web…"),
        _p("✍️ Writing your answer…"),
        _a("The capital of France is Paris."),
    ))
    # The answer body must be exactly the answer chunk(s) — no progress text.
    answer_calls = [
        c.kwargs["text"] for c in bot.send_message.await_args_list
        if "Paris" in c.kwargs.get("text", "")
    ]
    assert answer_calls, "answer was not delivered"
    for text in answer_calls:
        assert "Searching the web" not in text
        assert "Writing your answer" not in text


@pytest.mark.asyncio
async def test_status_message_and_typing_issued() -> None:
    adapter, bot = _adapter()
    await adapter.send(_stream(_p("🔎 Searching the web…"), _a("Answer.")))
    # A status message was sent (parse_mode=None) AND a typing action issued.
    status_sends = [
        c for c in bot.send_message.await_args_list
        if "Searching the web" in c.kwargs.get("text", "")
    ]
    assert len(status_sends) == 1
    assert status_sends[0].kwargs["parse_mode"] is None
    bot.send_chat_action.assert_awaited()


@pytest.mark.asyncio
async def test_settle_edits_status_into_done_footer() -> None:
    adapter, bot = _adapter()
    await adapter.send(_stream(_p("🔎 Searching…"), _a("Answer.")))
    # The status message (id 777) is edited into the done footer at settle.
    bot.edit_message_text.assert_awaited()
    last = bot.edit_message_text.await_args
    assert last.kwargs["message_id"] == 777
    assert "done in" in last.kwargs["text"]


@pytest.mark.asyncio
async def test_no_progress_chunks_is_byte_identical_single_send() -> None:
    adapter, bot = _adapter()
    await adapter.send(_stream(_a("Hello "), _a("world.")))
    # Exactly one outbound message (the joined answer); no status, no edit, no typing.
    assert bot.send_message.await_count == 1
    # Body is the joined answer (MarkdownV2-escaped by the existing formatter).
    assert "Hello world" in bot.send_message.await_args.kwargs["text"]
    bot.edit_message_text.assert_not_awaited()
    bot.send_chat_action.assert_not_awaited()
