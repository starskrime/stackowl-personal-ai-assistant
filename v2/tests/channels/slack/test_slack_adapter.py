"""Story 9.7 — SlackChannelAdapter unit tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.helpers import (
    hash_user_id,
    is_authorized,
    strip_bot_mention,
)
from stackowl.channels.slack.settings import SlackSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.streaming import ResponseChunk

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


def test_settings_app_token_default_empty() -> None:
    s = SlackSettings()
    assert s.app_token == ""


def test_settings_app_token_accepted() -> None:
    s = SlackSettings(app_token="xapp-1-abc")
    assert s.app_token == "xapp-1-abc"


def test_settings_app_token_marked_sensitive() -> None:
    # The field carries the sensitive marker so the redactor can hide it.
    extra = SlackSettings.model_fields["app_token"].json_schema_extra
    assert isinstance(extra, dict)
    assert extra.get("sensitive") is True


def test_settings_app_token_frozen() -> None:
    s = SlackSettings(app_token="xapp-1-abc")
    with pytest.raises(ValidationError):
        s.app_token = "mutated"  # type: ignore[misc]


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


# --------------------------------------------------------------------------- #
# A3 — per-message target routing + session→target map + threading
# --------------------------------------------------------------------------- #


class _FakeClient:
    """Captures every chat_postMessage kwargs dict the adapter emits."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"ok": True}


class _FakeApp:
    def __init__(self) -> None:
        self.client = _FakeClient()


def _chunk(content: str, *, target: int | str | None = None) -> ResponseChunk:
    return ResponseChunk(
        content=content,
        is_final=True,
        chunk_index=0,
        trace_id="t-test",
        owl_name="owl",
        target=target,
    )


async def _drain(*chunks: ResponseChunk) -> AsyncIterator[ResponseChunk]:
    for c in chunks:
        yield c


def _attach_live(adapter: SlackChannelAdapter) -> _FakeApp:
    """Attach a fake Bolt app and disengage the test-mode guard for I/O."""
    app = _FakeApp()
    adapter.set_bolt_app(app)
    return app


@pytest.mark.asyncio
async def test_handle_event_stamps_channel_target(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C123", "ts": "1.2"},
        user_id="U_allowed",
        text="hello",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    # The routing destination (channel id) is stamped on chat_id.
    assert msg.chat_id == "C123"
    # Session→target map records the channel id for Phase B to resolve.
    assert adapter.target_for_session(msg.session_id) == "C123"


@pytest.mark.asyncio
async def test_handle_event_unknown_session_returns_none() -> None:
    adapter = _make_adapter()
    assert adapter.target_for_session("slack:deadbeef") is None


@pytest.mark.asyncio
async def test_send_posts_to_resolved_channel_in_thread() -> None:
    """A channel event (thread_ts present) → reply in-thread to that channel."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C123", "thread_ts": "111.222", "ts": "333.444"},
        user_id="U_allowed",
        text="hi",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send(_drain(_chunk("reply body", target=msg.chat_id)))
    assert len(app.client.calls) == 1
    call = app.client.calls[0]
    assert call["channel"] == "C123"
    assert call["channel"] != "@stackowl"
    # thread_ts threads the reply under the originating thread.
    assert call["thread_ts"] == "111.222"
    assert call["text"] == "reply body"


@pytest.mark.asyncio
async def test_send_dm_replies_to_channel_no_thread() -> None:
    """A DM event (no thread_ts) → reply to the channel, no thread_ts kwarg."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "D999"},
        user_id="U_allowed",
        text="hey",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    assert msg.chat_id == "D999"
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send(_drain(_chunk("pong", target=msg.chat_id)))
    assert len(app.client.calls) == 1
    call = app.client.calls[0]
    assert call["channel"] == "D999"
    # No thread → thread_ts must NOT be passed (or be None).
    assert call.get("thread_ts") is None


@pytest.mark.asyncio
async def test_send_text_explicit_target() -> None:
    adapter = _make_adapter()
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send_text("direct", target="C777")
    assert len(app.client.calls) == 1
    assert app.client.calls[0]["channel"] == "C777"
    assert app.client.calls[0]["channel"] != "@stackowl"


@pytest.mark.asyncio
async def test_send_falls_back_to_last_target() -> None:
    """No explicit chunk target → adapter uses the last inbound target."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C555", "thread_ts": "9.9", "ts": "9.9"},
        user_id="U_allowed",
        text="x",
    )
    await asyncio.wait_for(adapter.receive(), timeout=1.0)
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send(_drain(_chunk("no-target")))
    assert len(app.client.calls) == 1
    assert app.client.calls[0]["channel"] == "C555"
    assert app.client.calls[0]["thread_ts"] == "9.9"


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
