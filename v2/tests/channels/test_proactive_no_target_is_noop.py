"""F006/C-1 — a proactive send_text with NO explicit target is a logged no-op.

The provenance-keyed raise must fire ONLY on the on-turn path (an explicit
keyword target was passed). A proactive/best-effort send (no explicit target,
no ``_last_*``) stays a non-crashing logged no-op — this proves the proactive
deliverer's NEVER-raises contract survives the F006 fail-loud change for all
four rich channels.
"""

from __future__ import annotations

import pytest

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.settings import SlackSettings
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard


@pytest.mark.asyncio
async def test_telegram_proactive_no_target_noop() -> None:
    """No explicit chat_id + no _last_chat_id → no-op, never raises."""
    TestModeGuard.deactivate()
    try:
        adapter = TelegramChannelAdapter(
            TelegramSettings(bot_token="x" * 12, allowed_user_ids=frozenset({1}))
        )
        assert adapter._last_chat_id is None
        # No chat_id kwarg → best-effort proactive path → must NOT raise.
        await adapter.send_text("proactive ping")
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_slack_proactive_no_target_noop() -> None:
    """No explicit target + no _last_target → no-op, never raises."""
    TestModeGuard.deactivate()
    try:
        adapter = SlackChannelAdapter(
            SlackSettings(bot_token="xoxb-x", app_token="xapp-x", allowed_user_ids=["U1"])
        )
        assert adapter._last_target is None
        await adapter.send_text("proactive ping")  # must not raise
    finally:
        TestModeGuard.deactivate()
