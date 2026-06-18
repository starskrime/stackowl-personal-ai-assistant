"""F005 merge-gate — Discord consent gate (rich View/Button round-trip).

A consequential action over Discord must render an approve/deny prompt and only
proceed on an approve interaction; a deny blocks it. This drives the REAL
:class:`DiscordConsentPrompter` ↔ adapter round-trip, mocking only the discord.py
``channel.send`` (which returns a message handle) and synthesising a button
"interaction" by invoking the registered callback with the approve/deny
``custom_id`` — exactly what discord.ui.Button does on a tap.

Fail-closed regression: the prompter targets the INITIATING user's channel
(resolved from ``_targets``); when no channel resolves it denies without a send.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from stackowl.channels.discord.adapter import DiscordChannelAdapter
from stackowl.channels.discord.consent import DiscordConsentPrompter
from stackowl.channels.discord.settings import DiscordSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.tools.consent import ConsentRequest, ConsentScope


def _adapter() -> DiscordChannelAdapter:
    return DiscordChannelAdapter(
        DiscordSettings(bot_token="x" * 8, allowed_user_ids=[11])
    )


async def _seed_inbound(adapter: DiscordChannelAdapter, user_id: int, channel_id: int) -> AsyncMock:
    """Register a session→channel target by driving one inbound message."""
    send = AsyncMock(return_value=types.SimpleNamespace(id=555))
    channel = types.SimpleNamespace(id=channel_id, send=send)
    author = types.SimpleNamespace(id=user_id)
    msg = types.SimpleNamespace(content="hi", author=author, channel=channel)
    await adapter.handle_message(msg)
    await adapter._queue.get()
    return send


@pytest.mark.asyncio
async def test_consequential_action_approved_grants_scope() -> None:
    """An approve interaction resolves the parked consent Future to a grant."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        send = await _seed_inbound(adapter, user_id=11, channel_id=4242)
        prompter = DiscordConsentPrompter(adapter)

        req = ConsentRequest(
            tool_name="shell",
            channel="discord",
            session_id="11",
            summary="rm -rf /tmp/x",
            allow_relaxation=True,
        )
        task = asyncio.create_task(prompter.prompt(req))
        await asyncio.sleep(0)  # let prompt() send + park

        # The prompt was sent with an interactive keyboard.
        send.assert_awaited()
        # Synthesise an approve tap: invoke the registered callback with the
        # approve custom_id the prompter drew.
        rid = next(iter(prompter._pending))
        await prompter.handle_callback("", f"consent:{rid}:{ConsentScope.SESSION.value}")

        scope = await asyncio.wait_for(task, timeout=2.0)
        assert scope == ConsentScope.SESSION
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_consequential_action_denied_blocks() -> None:
    """A deny interaction resolves the parked consent Future to DENY."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        await _seed_inbound(adapter, user_id=11, channel_id=4242)
        prompter = DiscordConsentPrompter(adapter)
        req = ConsentRequest(
            tool_name="shell", channel="discord", session_id="11",
            summary="danger", allow_relaxation=True,
        )
        task = asyncio.create_task(prompter.prompt(req))
        await asyncio.sleep(0)
        rid = next(iter(prompter._pending))
        await prompter.handle_callback("", f"consent:{rid}:{ConsentScope.DENY_SESSION.value}")
        scope = await asyncio.wait_for(task, timeout=2.0)
        assert scope == ConsentScope.DENY_SESSION
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_consent_fails_closed_when_no_channel_resolves() -> None:
    """No session→channel target → deny WITHOUT sending a prompt (never guess)."""
    TestModeGuard.deactivate()
    try:
        adapter = _adapter()
        prompter = DiscordConsentPrompter(adapter)
        req = ConsentRequest(
            tool_name="shell", channel="discord", session_id="unknown",
            summary="x", allow_relaxation=True,
        )
        scope = await prompter.prompt(req)
        assert scope == ConsentScope.DENY
    finally:
        TestModeGuard.deactivate()
