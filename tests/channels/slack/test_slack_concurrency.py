"""CONC-1 (F010) + CONC-2 (F011) — Slack per-turn state must be turn-owned, not
clobbered by a newer concurrent same-user / same-channel event.

F010: inbound file ids were keyed by ``session_id`` (``slack:{hash(user)}`` — the
SAME for every message from a user across channels/threads). A later FILELESS
event from the same user popped the earlier turn's ids before it fetched them.
Fix: key inbound files by ``trace_id`` (the turn owns it).

F011: reply thread fell back to a global ``_last_thread`` when ``dest ==
_last_target`` — a newer concurrent event for the same channel could set a
DIFFERENT thread, mis-threading the earlier turn's reply. Fix: resolve thread_ts
per-turn (trace_id-keyed) and drop the _last_thread fallback.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.settings import SlackSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.streaming import ResponseChunk


def _make_adapter() -> SlackChannelAdapter:
    return SlackChannelAdapter(
        SlackSettings(
            bot_token="xoxb-test",
            signing_secret="sig",
            allowed_user_ids=["Ualice"],
        )
    )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"ok": True}


class _FakeApp:
    def __init__(self) -> None:
        self.client = _FakeClient()


async def _drain(*chunks: ResponseChunk) -> object:
    async def _gen() -> object:
        for c in chunks:
            yield c

    return _gen()


@pytest.mark.asyncio
async def test_second_fileless_event_does_not_wipe_first_turns_files() -> None:
    adapter = _make_adapter()
    # Turn 1: same user, WITH a file attachment.
    await adapter.handle_event(
        {"type": "message", "channel": "C1", "ts": "1.0",
         "files": [{"id": "F_first"}]},
        user_id="Ualice", text="here is a file",
    )
    msg1 = await asyncio.wait_for(adapter.receive(), timeout=1.0)

    # Turn 2: SAME user (same session_id), NO file — must not clear turn 1's ids.
    await adapter.handle_event(
        {"type": "message", "channel": "C1", "ts": "2.0"},
        user_id="Ualice", text="now a plain message",
    )
    msg2 = await asyncio.wait_for(adapter.receive(), timeout=1.0)

    # Turn 1's files are still resolvable BY ITS TRACE; turn 2 has none.
    assert adapter.inbound_files_for_trace(msg1.trace_id) == ["F_first"]
    assert adapter.inbound_files_for_trace(msg2.trace_id) == []


@pytest.mark.asyncio
async def test_concurrent_channel_reply_threads_to_its_own_turn() -> None:
    """Two events on the SAME channel with DIFFERENT threads: each turn's reply
    must thread under ITS originating thread, never the newer event's thread."""
    adapter = _make_adapter()
    app = _FakeApp()
    adapter.set_bolt_app(app)

    # Event 1 — channel C1, thread T_a.
    await adapter.handle_event(
        {"type": "message", "channel": "C1", "thread_ts": "T_a", "ts": "1.0"},
        user_id="Ualice", text="first",
    )
    msg1 = await asyncio.wait_for(adapter.receive(), timeout=1.0)

    # Event 2 — SAME channel C1, DIFFERENT thread T_b (newer; would poison a
    # global _last_thread / overwrite a per-channel thread map).
    await adapter.handle_event(
        {"type": "message", "channel": "C1", "thread_ts": "T_b", "ts": "2.0"},
        user_id="Ualice", text="second",
    )
    msg2 = await asyncio.wait_for(adapter.receive(), timeout=1.0)

    try:
        # Turn 1 replies AFTER event 2 arrived — must still use T_a.
        await adapter.send(
            await _drain(
                ResponseChunk(content="reply one", is_final=True, chunk_index=0,
                              trace_id=msg1.trace_id, owl_name="o", target="C1")
            )
        )
        await adapter.send(
            await _drain(
                ResponseChunk(content="reply two", is_final=True, chunk_index=0,
                              trace_id=msg2.trace_id, owl_name="o", target="C1")
            )
        )
    finally:
        TestModeGuard.deactivate()

    threads_by_text = {
        str(c.get("text", "")).split()[-1]: c.get("thread_ts")
        for c in app.client.calls
    }
    assert threads_by_text["one"] == "T_a", f"turn1 mis-threaded: {app.client.calls!r}"
    assert threads_by_text["two"] == "T_b", f"turn2 mis-threaded: {app.client.calls!r}"
