"""Story 9.7 — SlackChannelAdapter unit tests."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.helpers import (
    hash_user_id,
    is_authorized,
    strip_bot_mention,
)
from stackowl.channels.slack.settings import SlackSettings


# --------------------------------------------------------------------------- #
# Pure-helper tests
# --------------------------------------------------------------------------- #


def test_strip_bot_mention() -> None:
    cleaned = strip_bot_mention("<@U123> hello", "U123")
    assert cleaned == "hello"


def test_strip_bot_mention_no_match_passthrough() -> None:
    # When the mention isn't present we still strip surrounding whitespace.
    assert strip_bot_mention("  hello world  ", "U999") == "hello world"


def test_is_authorized_true() -> None:
    assert is_authorized("U123", ["U123", "U456"]) is True


def test_is_authorized_false() -> None:
    assert is_authorized("U999", ["U123", "U456"]) is False
    # Fail-closed: empty allow-list rejects everything.
    assert is_authorized("U123", []) is False


def test_hash_user_id() -> None:
    h = hash_user_id("U12345")
    assert isinstance(h, str)
    assert len(h) == 8
    # Hex digits only
    int(h, 16)


# --------------------------------------------------------------------------- #
# Settings tests
# --------------------------------------------------------------------------- #


def test_settings_frozen() -> None:
    s = SlackSettings(bot_token="x", signing_secret="y", allowed_user_ids=["U1"])
    with pytest.raises(ValidationError):
        s.bot_token = "mutated"  # type: ignore[misc]


def test_settings_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        SlackSettings(unknown_field=True)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# Adapter tests
# --------------------------------------------------------------------------- #


def _make_adapter(allowed: list[str] | None = None) -> SlackChannelAdapter:
    return SlackChannelAdapter(
        SlackSettings(
            bot_token="xoxb-test",
            signing_secret="sig",
            allowed_user_ids=allowed if allowed is not None else ["U123"],
        )
    )


def test_adapter_channel_name() -> None:
    adapter = _make_adapter()
    assert adapter.channel_name == "slack"


@pytest.mark.asyncio
async def test_handle_event_unauthorized() -> None:
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message"},
        user_id="U_blocked",
        text="hello",
    )
    # Queue must remain empty — message was silently dropped.
    assert adapter._queue.qsize() == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_handle_event_authorized() -> None:
    adapter = _make_adapter(allowed=["U_allowed"])
    adapter.set_bot_user_id("U_bot")
    await adapter.handle_event(
        {"type": "message"},
        user_id="U_allowed",
        text="<@U_bot> hello world",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    assert msg.text == "hello world"
    assert msg.channel == "slack"
    # Session_id includes the hashed (never raw) user_id.
    assert msg.session_id.startswith("slack:")
    assert "U_allowed" not in msg.session_id


@pytest.mark.asyncio
async def test_health_check_no_ping() -> None:
    adapter = _make_adapter()
    status = await adapter.health_check()
    assert status.status == "degraded"
    assert status.name == "slack_channel"


@pytest.mark.asyncio
async def test_health_check_with_recent_ping() -> None:
    adapter = _make_adapter()
    adapter.mark_ping()
    status = await adapter.health_check()
    assert status.status == "ok"
