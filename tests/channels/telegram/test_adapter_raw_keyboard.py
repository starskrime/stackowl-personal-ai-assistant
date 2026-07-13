"""Telegram adapter must dispatch a chunk's ``raw_keyboard`` as the message's
inline keyboard (bypassing the Action-shaped ``build_command_keyboard`` path)
and backfill the sent message's (chat_id, message_id) into the SAME
``ApproachRatingTracker`` singleton `consolidate.py` recorded the pending vote
on — see task-6 brief."""

from __future__ import annotations

import asyncio
import types
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.pipeline.services import StepServices, get_services, reset_services, set_services
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio

_KEYBOARD = {"inline_keyboard": [[{"text": "\U0001F44D", "callback_data": "apr:trace-9:positive"}]]}


def _settings() -> TelegramSettings:
    return TelegramSettings(bot_token="x" * 20, allowed_user_ids=frozenset({42}))


def _adapter(message_id: int = 777) -> tuple[TelegramChannelAdapter, MagicMock]:
    adapter = TelegramChannelAdapter(_settings())
    bot = MagicMock()
    bot.send_message = AsyncMock(
        return_value=types.SimpleNamespace(message_id=message_id)
    )
    adapter._bot_app = types.SimpleNamespace(bot=bot)
    return adapter, bot


async def _stream(*chunks: ResponseChunk) -> AsyncIterator[ResponseChunk]:
    for c in chunks:
        yield c


async def test_send_with_raw_keyboard_backfills_tracker() -> None:
    adapter, bot = _adapter(message_id=777)
    tracker = MagicMock()
    tracker.backfill_message = AsyncMock()
    token = set_services(StepServices(approach_rating_tracker=tracker))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="x" * 250,
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-9",
                    owl_name="secretary",
                    target=321,
                    is_floor=False,
                    raw_keyboard=_KEYBOARD,
                )
            )
        )
    finally:
        reset_services(token)

    tracker.backfill_message.assert_awaited_once_with(
        trace_id="trace-9", chat_id=321, message_id=777
    )
    # Delivered via the raw-keyboard path (send_message with a reply_markup),
    # never through build_command_keyboard.
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["reply_markup"] is not None


async def test_send_without_raw_keyboard_does_not_touch_tracker() -> None:
    adapter, bot = _adapter(message_id=1)
    tracker = MagicMock()
    tracker.backfill_message = AsyncMock()
    token = set_services(StepServices(approach_rating_tracker=tracker))
    try:
        await adapter.send(
            _stream(
                ResponseChunk(
                    content="a short reply",
                    is_final=True,
                    chunk_index=0,
                    trace_id="trace-10",
                    owl_name="o",
                    target=321,
                )
            )
        )
    finally:
        reset_services(token)

    tracker.backfill_message.assert_not_awaited()
    bot.send_message.assert_awaited_once()


# --- cross-task ambient-context regression (live-bugfix-backfill-nulls) ----
#
# Production never calls ``adapter.send()`` in the SAME task that called
# ``set_services()`` — ``ClarifyPump.spawn_send`` (clarify_pump.py) always
# drives it via a dedicated ``asyncio.create_task(channel_adapter.send(reader))``
# "send task", a SIBLING of the turn's own producer task, both spawned from a
# shared ``_dispatch_turn`` call. ``asyncio.create_task`` copies the CALLING
# coroutine's ``contextvars.Context`` at creation time, so a per-turn
# ``set_services()`` called INSIDE the producer task's own body (as
# ``AsyncioBackend.run()`` does) is invisible to that sibling send task unless
# the common ANCESTOR scope already had ``set_services()`` bound before either
# task was created. The two tests above never exercise this boundary — they
# call ``set_services`` in the same task that directly ``await``s
# ``adapter.send()``, which is why this gap shipped invisibly.


async def test_send_in_sibling_task_without_ancestor_binding_never_backfills() -> None:
    """Characterizes the pre-fix production shape: no ancestor-level
    ``set_services()`` exists, so a sibling "send task" (mirroring
    ``ClarifyPump.spawn_send``) sees an EMPTY ``StepServices()`` and the
    approach-rating backfill silently no-ops — the exact mechanism behind the
    "vote recorded but no message location — edit skipped" symptom."""
    adapter, bot = _adapter(message_id=555)
    tracker = MagicMock()
    tracker.backfill_message = AsyncMock()

    async def ancestor() -> None:
        # Ancestor scope never calls set_services — matches the un-fixed
        # _dispatch_turn/_phase_gateway shape.
        async def producer() -> None:
            # Mirrors AsyncioBackend.run(): set/reset scoped to ITS OWN task.
            token = set_services(StepServices(approach_rating_tracker=tracker))
            try:
                await asyncio.sleep(0)
            finally:
                reset_services(token)

        async def send_task() -> None:
            await adapter.send(
                _stream(
                    ResponseChunk(
                        content="x" * 250, is_final=True, chunk_index=0,
                        trace_id="trace-sibling-nofix", owl_name="secretary",
                        target=321, is_floor=False, raw_keyboard=_KEYBOARD,
                    )
                )
            )

        await asyncio.gather(
            asyncio.create_task(producer()), asyncio.create_task(send_task())
        )

    await ancestor()

    tracker.backfill_message.assert_not_awaited()
    bot.send_message.assert_awaited_once()  # the keyboard was still sent — only the backfill is lost


async def test_send_in_sibling_task_backfills_when_services_bound_in_ancestor_scope() -> None:
    """Regression test for the orchestrator.py fix: ``_phase_gateway`` now
    binds the shared ``StepServices`` via ``set_services()`` ONCE, in its own
    (ancestor) scope, before spawning any channel-loop / turn-dispatch tasks.
    A sibling "send task" created afterwards (matching ``ClarifyPump.spawn_send``)
    must observe it via ``get_services()`` even though the per-turn producer
    task independently sets/resets its OWN copy around its own body."""
    adapter, bot = _adapter(message_id=556)
    tracker = MagicMock()
    tracker.backfill_message = AsyncMock()

    async def ancestor() -> None:
        # The fix: bind once in the ancestor, mirroring _phase_gateway.
        set_services(StepServices(approach_rating_tracker=tracker))

        async def producer() -> None:
            token = set_services(StepServices(approach_rating_tracker=tracker))
            try:
                await asyncio.sleep(0)
            finally:
                reset_services(token)

        async def send_task() -> None:
            # Sanity check that the sibling task really does inherit the
            # ancestor binding rather than an empty StepServices().
            assert get_services().approach_rating_tracker is tracker
            await adapter.send(
                _stream(
                    ResponseChunk(
                        content="x" * 250, is_final=True, chunk_index=0,
                        trace_id="trace-sibling-fixed", owl_name="secretary",
                        target=321, is_floor=False, raw_keyboard=_KEYBOARD,
                    )
                )
            )

        await asyncio.gather(
            asyncio.create_task(producer()), asyncio.create_task(send_task())
        )

    await ancestor()

    tracker.backfill_message.assert_awaited_once_with(
        trace_id="trace-sibling-fixed", chat_id=321, message_id=556
    )
    bot.send_message.assert_awaited_once()
