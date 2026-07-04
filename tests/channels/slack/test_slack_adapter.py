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
    """Captures every chat_postMessage / files_* kwargs dict the adapter emits."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.upload_calls: list[dict[str, object]] = []
        self.info_calls: list[dict[str, object]] = []
        self.url_private: str = "https://files.slack.test/u/url_private"

    async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"ok": True}

    async def files_upload_v2(self, **kwargs: object) -> dict[str, object]:
        self.upload_calls.append(dict(kwargs))
        return {"ok": True}

    async def files_info(self, **kwargs: object) -> dict[str, object]:
        self.info_calls.append(dict(kwargs))
        return {"ok": True, "file": {"id": kwargs.get("file"), "url_private": self.url_private}}


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


# --------------------------------------------------------------------------- #
# B2 — send_clarify Block Kit buttons + target resolution + text fallback
# --------------------------------------------------------------------------- #


async def _seed_session(adapter: SlackChannelAdapter, channel: str) -> str:
    """Drive an inbound event so the session→target map is populated."""
    await adapter.handle_event(
        {"type": "message", "channel": channel},
        user_id="U_allowed",
        text="hi",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    return msg.session_id


def _actions_elements(call: dict[str, object]) -> list[dict]:
    blocks = call.get("blocks")
    if not isinstance(blocks, list):
        # A text-fallback post carries no blocks → no action buttons.
        return []
    elements: list[dict] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "actions":
            elements.extend(block.get("elements", []))
    return elements


@pytest.mark.asyncio
async def test_send_clarify_posts_block_kit_buttons() -> None:
    """One button per non-blank choice, action_id/value = clarify:{cid}:{idx}."""
    adapter = _make_adapter(allowed=["U_allowed"])
    session_id = await _seed_session(adapter, "C123")
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    await adapter.send_clarify(
        session_id,
        question="Pick a color",
        choices=("red", "green", "blue"),
        clarify_id="cid",
    )

    assert len(app.client.calls) == 1
    call = app.client.calls[0]
    assert call["channel"] == "C123"
    elements = _actions_elements(call)
    assert len(elements) == 3
    assert [e["action_id"] for e in elements] == [
        "clarify:cid:0",
        "clarify:cid:1",
        "clarify:cid:2",
    ]
    assert [e["value"] for e in elements] == [
        "clarify:cid:0",
        "clarify:cid:1",
        "clarify:cid:2",
    ]


@pytest.mark.asyncio
async def test_send_clarify_preserves_original_index_with_blanks() -> None:
    """Blank choices are skipped but the ORIGINAL index is preserved in the id."""
    adapter = _make_adapter(allowed=["U_allowed"])
    session_id = await _seed_session(adapter, "C123")
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    await adapter.send_clarify(
        session_id,
        question="Q",
        choices=("a", "", "c"),
        clarify_id="cid",
    )

    elements = _actions_elements(app.client.calls[0])
    # The blank middle choice is skipped, but indices 0 and 2 are preserved.
    assert [e["action_id"] for e in elements] == ["clarify:cid:0", "clarify:cid:2"]


@pytest.mark.asyncio
async def test_send_clarify_unresolved_target_degrades_to_text() -> None:
    """No target for the session → degrade to the numbered-text fallback."""
    adapter = _make_adapter(allowed=["U_allowed"])
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    # Pin a fallback terminal so the text path has somewhere to land.
    adapter._last_target = "C_fallback"  # type: ignore[attr-defined]

    # Session never seen → target_for_session returns None.
    await adapter.send_clarify(
        "slack:unknownsession",
        question="Pick",
        choices=("a", "b"),
        clarify_id="cid",
    )

    # A message was still delivered (text fallback), and it carries NO buttons.
    assert len(app.client.calls) == 1
    call = app.client.calls[0]
    assert _actions_elements(call) == []
    text = call.get("text")
    assert isinstance(text, str)
    # The numbered fallback lists each choice.
    assert "a" in text
    assert "b" in text


@pytest.mark.asyncio
async def test_send_clarify_no_choices_sends_plain_text() -> None:
    """Open-ended question (no non-blank choices) → plain text, no buttons."""
    adapter = _make_adapter(allowed=["U_allowed"])
    session_id = await _seed_session(adapter, "C123")
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    await adapter.send_clarify(
        session_id,
        question="What is your name?",
        choices=(),
        clarify_id="cid",
    )

    assert len(app.client.calls) == 1
    assert _actions_elements(app.client.calls[0]) == []


@pytest.mark.asyncio
async def test_send_clarify_post_failure_degrades_to_text() -> None:
    """A Block Kit post failure self-heals to the text fallback."""

    class _FailThenOkClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self._first = True

        async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(dict(kwargs))
            if self._first and kwargs.get("blocks"):
                self._first = False
                raise RuntimeError("blocks rejected")
            return {"ok": True}

    class _FailApp:
        def __init__(self) -> None:
            self.client = _FailThenOkClient()

    adapter = _make_adapter(allowed=["U_allowed"])
    session_id = await _seed_session(adapter, "C123")
    app = _FailApp()
    adapter.set_bolt_app(app)
    TestModeGuard.deactivate()

    await adapter.send_clarify(
        session_id,
        question="Pick",
        choices=("a", "b"),
        clarify_id="cid",
    )

    # First (block) post failed; a second text-fallback post was attempted.
    assert len(app.client.calls) == 2
    assert _actions_elements(app.client.calls[1]) == []


# --------------------------------------------------------------------------- #
# C1 — send_file (files_upload_v2 + thread) + download_media (authorized fetch)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_file_uploads_to_resolved_channel_in_thread(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """send_file resolves the per-channel thread and uploads via files_upload_v2."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C123", "thread_ts": "111.222", "ts": "333.444"},
        user_id="U_allowed",
        text="hi",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 data")
    await adapter.send_file(str(f), caption="the report", target=msg.chat_id)

    assert len(app.client.upload_calls) == 1
    call = app.client.upload_calls[0]
    assert call["channel"] == "C123"
    assert call["thread_ts"] == "111.222"
    assert call["file"] == str(f)
    assert call["initial_comment"] == "the report"


@pytest.mark.asyncio
async def test_send_file_dm_no_thread(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A DM event (no thread) → upload to the channel with no thread_ts kwarg."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "D999"},
        user_id="U_allowed",
        text="hey",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    f = tmp_path / "note.txt"
    f.write_bytes(b"hello")
    await adapter.send_file(str(f), target=msg.chat_id)

    assert len(app.client.upload_calls) == 1
    call = app.client.upload_calls[0]
    assert call["channel"] == "D999"
    assert call.get("thread_ts") is None
    # No caption → initial_comment is None.
    assert call.get("initial_comment") is None


@pytest.mark.asyncio
async def test_send_file_falls_back_to_last_target(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No explicit target → upload to the last inbound channel."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C555"},
        user_id="U_allowed",
        text="x",
    )
    await asyncio.wait_for(adapter.receive(), timeout=1.0)
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01")
    await adapter.send_file(str(f))

    assert len(app.client.upload_calls) == 1
    assert app.client.upload_calls[0]["channel"] == "C555"


@pytest.mark.asyncio
async def test_send_file_no_client_is_graceful_noop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No Bolt app attached → graceful no-op (no crash, no upload)."""
    adapter = _make_adapter(allowed=["U_allowed"])
    adapter._last_target = "C123"  # type: ignore[attr-defined]
    TestModeGuard.deactivate()
    f = tmp_path / "x.txt"
    f.write_bytes(b"y")
    # Must not raise even though no app is attached.
    await adapter.send_file(str(f))


@pytest.mark.asyncio
async def test_send_file_no_target_is_graceful_noop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No resolvable destination → graceful no-op (no upload attempted)."""
    adapter = _make_adapter()
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    f = tmp_path / "x.txt"
    f.write_bytes(b"y")
    await adapter.send_file(str(f))  # _last_target is None
    assert len(app.client.upload_calls) == 0


@pytest.mark.asyncio
async def test_send_file_test_mode_guard_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """TestModeGuard active → send_file raises (live-IO guard)."""
    adapter = _make_adapter(allowed=["U_allowed"])
    _attach_live(adapter)
    adapter._last_target = "C123"  # type: ignore[attr-defined]
    f = tmp_path / "x.txt"
    f.write_bytes(b"y")
    from stackowl.config.test_mode import TestModeViolation

    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await adapter.send_file(str(f))
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_send_file_upload_error_does_not_crash(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An upload error is logged but never crashes the turn (self-healing)."""

    class _FailClient(_FakeClient):
        async def files_upload_v2(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("upload boom")

    class _FailApp:
        def __init__(self) -> None:
            self.client = _FailClient()

    adapter = _make_adapter(allowed=["U_allowed"])
    adapter.set_bolt_app(_FailApp())
    adapter._last_target = "C123"  # type: ignore[attr-defined]
    TestModeGuard.deactivate()
    f = tmp_path / "x.txt"
    f.write_bytes(b"y")
    # Must swallow the error (logged), not propagate.
    await adapter.send_file(str(f))


@pytest.mark.asyncio
async def test_download_media_fetches_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """download_media resolves url_private via files_info then authorized GET."""
    adapter = _make_adapter(allowed=["U_allowed"])
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    captured: dict[str, object] = {}

    class _FakeResponse:
        content = b"FILEBYTES"

        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    data = await adapter.download_media("F123")
    assert data == b"FILEBYTES"
    # files_info was queried for the url_private.
    assert app.client.info_calls[0]["file"] == "F123"
    # The GET targeted the resolved url_private with a Bearer auth header.
    assert captured["url"] == app.client.url_private
    assert captured["headers"]["Authorization"] == "Bearer xoxb-test"  # type: ignore[index]


@pytest.mark.asyncio
async def test_download_media_no_client_raises() -> None:
    """No Bolt client → download genuinely fails: raise loudly (no silent empty)."""
    adapter = _make_adapter()
    TestModeGuard.deactivate()
    with pytest.raises(RuntimeError):
        await adapter.download_media("F123")


@pytest.mark.asyncio
async def test_download_media_fetch_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fetch error surfaces loudly (per no-hidden-errors), not a silent b''."""
    adapter = _make_adapter(allowed=["U_allowed"])
    _attach_live(adapter)
    TestModeGuard.deactivate()

    class _BoomClient:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> _BoomClient:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> object:
            raise RuntimeError("network down")

    monkeypatch.setattr("httpx.AsyncClient", _BoomClient)
    with pytest.raises(RuntimeError):
        await adapter.download_media("F123")


# --------------------------------------------------------------------------- #
# C2 — outbound GFM→mrkdwn conversion + inbound files surfacing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_converts_gfm_bold_to_mrkdwn() -> None:
    """Outbound ``**bold**`` (GFM) is converted to ``*bold*`` (mrkdwn) before posting."""
    adapter = _make_adapter()
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send(_drain(_chunk("**hi**", target="C1")))
    assert len(app.client.calls) == 1
    assert app.client.calls[0]["text"] == "*hi*"


@pytest.mark.asyncio
async def test_send_text_converts_gfm_link_to_mrkdwn() -> None:
    """Outbound ``[t](u)`` (GFM) is converted to ``<u|t>`` (mrkdwn) before posting."""
    adapter = _make_adapter()
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send_text("[t](https://x.io)", target="C2")
    assert app.client.calls[0]["text"] == "<https://x.io|t>"


@pytest.mark.asyncio
async def test_send_text_leaves_code_span_literal() -> None:
    """Markup inside a code span is NOT converted on the outbound path."""
    adapter = _make_adapter()
    app = _attach_live(adapter)
    TestModeGuard.deactivate()
    await adapter.send_text("`**x**`", target="C3")
    assert app.client.calls[0]["text"] == "`**x**`"


@pytest.mark.asyncio
async def test_handle_event_surfaces_inbound_file_ids() -> None:
    """An event with a ``files`` array surfaces the file id(s) per TURN (trace)."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {
            "type": "message",
            "channel": "C123",
            "files": [{"id": "F111"}, {"id": "F222"}],
        },
        user_id="U_allowed",
        text="see attached",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    assert adapter.inbound_files_for_trace(msg.trace_id) == ["F111", "F222"]


@pytest.mark.asyncio
async def test_handle_event_no_files_means_empty() -> None:
    """An event without files surfaces no file ids (no fabricated entries)."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C123"},
        user_id="U_allowed",
        text="plain",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    assert adapter.inbound_files_for_trace(msg.trace_id) == []


# --------------------------------------------------------------------------- #
# F-64 — on-turn transport failure must NOT be silently swallowed
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_text_transport_failure_raises_delivery_error() -> None:
    """An on-turn chat_postMessage failure must surface as DeliveryError.

    Previously _post_text swallowed every transport error and returned
    normally, so the deliverer/ledger recorded a clean send while the user's
    reply never arrived. The on-turn send_text path must re-raise so the
    deliverer records ``failed`` and can retry.
    """
    from stackowl.exceptions import DeliveryError

    class _BoomClient(_FakeClient):
        async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("network down")

    class _BoomApp:
        def __init__(self) -> None:
            self.client = _BoomClient()

    adapter = _make_adapter()
    adapter.set_bolt_app(_BoomApp())
    TestModeGuard.deactivate()

    with pytest.raises(DeliveryError) as exc_info:
        await adapter.send_text("on-turn reply", target="C_live")
    # Coarse reason only — never a raw channel id / secret.
    assert exc_info.value.reason == "transport_error"
    assert exc_info.value.channel == "slack"


@pytest.mark.asyncio
async def test_post_text_best_effort_swallows_when_flagged() -> None:
    """A best-effort caller (raise_on_error=False) still tolerates failure."""

    class _BoomClient(_FakeClient):
        async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("rate limited or whatever")

    class _BoomApp:
        def __init__(self) -> None:
            self.client = _BoomClient()

    adapter = _make_adapter()
    adapter.set_bolt_app(_BoomApp())
    TestModeGuard.deactivate()

    # Must NOT raise — best-effort path swallows (logged).
    await adapter._post_text(  # type: ignore[attr-defined]
        "best effort", index=0, channel="C_be", raise_on_error=False
    )


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


# --------------------------------------------------------------------------- #
# Progress-chunk filtering
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_filters_progress_chunks() -> None:
    """Progress chunks should never be added to the delivered answer buffer."""
    adapter = _make_adapter(allowed=["U_allowed"])
    await adapter.handle_event(
        {"type": "message", "channel": "C123"},
        user_id="U_allowed",
        text="hi",
    )
    msg = await asyncio.wait_for(adapter.receive(), timeout=1.0)
    app = _attach_live(adapter)
    TestModeGuard.deactivate()

    # Mix of progress and answer chunks
    progress_chunk = ResponseChunk(
        content="[thinking...]",
        is_final=False,
        chunk_index=0,
        trace_id="t-test",
        owl_name="owl",
        target=msg.chat_id,
        kind="progress",  # Live status, should NOT appear in delivered message
    )
    answer_chunk = ResponseChunk(
        content="final answer",
        is_final=True,
        chunk_index=1,
        trace_id="t-test",
        owl_name="owl",
        target=msg.chat_id,
        kind="answer",  # Real answer, should appear in delivered message
    )

    await adapter.send(_drain(progress_chunk, answer_chunk))

    # Only one message should be posted (the answer)
    assert len(app.client.calls) == 1
    call = app.client.calls[0]
    # The delivered text must contain ONLY the answer, not the progress
    assert call["text"] == "final answer"
    assert "[thinking...]" not in call["text"]
