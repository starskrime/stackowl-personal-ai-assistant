"""F006/C-1 — Slack send target-narrowing + provenance-keyed fail-loud.

Slack delivers ONLY to str channel ids; a stray int target (Telegram chat_id)
cannot reach the Slack adapter by construction. On the on-turn ``send()`` path a
stray int narrows to None and, with no ``_last_target``, FAILS LOUD
(``DeliveryError("slack", "no_target")``) — a turn's answer is never silently
dropped. A genuine str target flows through; a stray int with a ``_last_target``
falls back (recoverable, no raise).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.settings import SlackSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DeliveryError
from stackowl.pipeline.streaming import ResponseChunk


def _adapter() -> SlackChannelAdapter:
    return SlackChannelAdapter(
        SlackSettings(bot_token="xoxb-x", app_token="xapp-x", allowed_user_ids=["U1"])
    )


def _attach(adapter: SlackChannelAdapter) -> MagicMock:
    app = MagicMock()
    app.client = MagicMock()
    app.client.chat_postMessage = AsyncMock()
    adapter.set_bolt_app(app)
    return app


def _chunk(content: str, target: int | str | None) -> ResponseChunk:
    return ResponseChunk(
        content=content, is_final=True, chunk_index=0,
        trace_id="t", owl_name="owl", target=target,
    )


async def _chunks(*chunks: ResponseChunk) -> Any:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_send_str_target_delivers_to_that_channel() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        app = _attach(adapter)
        await adapter.send(_chunks(_chunk("hello", "C123")))
        app.client.chat_postMessage.assert_awaited()
        assert app.client.chat_postMessage.await_args.kwargs["channel"] == "C123"
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_int_target_narrows_to_none_and_raises() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        app = _attach(adapter)
        assert adapter._last_target is None
        with pytest.raises(DeliveryError) as ei:
            await adapter.send(_chunks(_chunk("hi", 456)))
        assert ei.value.channel == "slack"
        assert ei.value.reason == "no_target"
        app.client.chat_postMessage.assert_not_awaited()
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_int_target_falls_back_to_last_target() -> None:
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        app = _attach(adapter)
        adapter._last_target = "C999"
        await adapter.send(_chunks(_chunk("hi", 789)))
        app.client.chat_postMessage.assert_awaited()
        assert app.client.chat_postMessage.await_args.kwargs["channel"] == "C999"
    finally:
        TestModeGuard.deactivate()
