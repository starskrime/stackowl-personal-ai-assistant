"""An injected turn takes the real path, and can never exceed a real one.

WHY THIS FACILITY EXISTS. Validating a fix needs traffic, and traffic that takes
a different path validates a path nobody uses. On 2026-08-25 I reached a running
instance by connecting to ``~/.stackowl/runtime/core.sock`` — and the GATEWAY
binds that socket while the CORE dials in as a client, so ``_accept_core`` took
me for the core reattaching and displaced the live link for three and a half
minutes. These tests pin the two properties that make the replacement safe: it
binds its OWN socket, and it authorises with the CHANNEL's rule.

WHAT IS DELIBERATELY NOT TESTED HERE: that the message reaches ``_handle_ingress``
and comes back over Telegram. That is not a unit-testable claim — it is the
acceptance check, and it is closed by a real injection against the running
gateway, with the ``injected: True`` INFO line as its evidence.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from stackowl.channels.dev_ingress import DevIngressListener, DevIngressTarget
from stackowl.gateway.scanner import IngressMessage

pytestmark = pytest.mark.asyncio

ALLOWED = "72055773"


class _SpyAdapter:
    """Stands in for the live TelegramChannelAdapter's queue side."""

    def __init__(self) -> None:
        self.fed: list[IngressMessage] = []

    def feed(self, msg: IngressMessage) -> None:
        self.fed.append(msg)


def _listener(tmp_path: Path, adapter: _SpyAdapter) -> DevIngressListener:
    return DevIngressListener(
        tmp_path / "dev-ingress.sock",
        {"telegram": DevIngressTarget(
            adapter=adapter, is_allowed=lambda k: k == ALLOWED
        )},
    )


async def _request(path: Path, payload: dict[str, object]) -> dict[str, object]:
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        result = json.loads(line.decode("utf-8"))
        assert isinstance(result, dict)
        return result
    finally:
        writer.close()


async def test_an_injected_turn_reaches_the_channel_queue(tmp_path: Path) -> None:
    """THE point of the facility."""
    adapter = _SpyAdapter()
    listener = _listener(tmp_path, adapter)
    await listener.start()
    try:
        reply = await _request(tmp_path / "dev-ingress.sock", {
            "channel": "telegram", "session_key": ALLOWED,
            "text": "what is the platform doing right now?", "chat_id": 72055773,
        })
    finally:
        await listener.stop()

    assert reply["ok"] is True
    assert len(adapter.fed) == 1
    msg = adapter.fed[0]
    assert msg.text == "what is the platform doing right now?"
    assert msg.session_key == ALLOWED, "the RAW handle — the shape ESC-17 depends on"
    assert msg.channel == "telegram"
    assert msg.chat_id == 72055773, "stamped, so the reply routes to its own chat"
    assert msg.trace_id == reply["trace_id"]


async def test_an_unauthorised_session_key_is_REFUSED(tmp_path: Path) -> None:
    """The guard that matters. An injected turn may never reach further than one
    that arrived over the network, so the channel's own allow-list decides."""
    adapter = _SpyAdapter()
    listener = _listener(tmp_path, adapter)
    await listener.start()
    try:
        reply = await _request(tmp_path / "dev-ingress.sock", {
            "channel": "telegram", "session_key": "999", "text": "let me in",
        })
    finally:
        await listener.stop()

    assert reply["ok"] is False
    assert adapter.fed == [], "nothing may be queued for an unauthorised key"


async def test_an_unknown_channel_is_refused(tmp_path: Path) -> None:
    adapter = _SpyAdapter()
    listener = _listener(tmp_path, adapter)
    await listener.start()
    try:
        reply = await _request(tmp_path / "dev-ingress.sock", {
            "channel": "slack", "session_key": ALLOWED, "text": "hello",
        })
    finally:
        await listener.stop()

    assert reply["ok"] is False
    assert adapter.fed == []


async def test_the_socket_is_owner_only(tmp_path: Path) -> None:
    """0600 IS the authentication. bind() creates the file with the process
    umask — usually 0644 — so the chmod after bind is load-bearing, not tidiness."""
    adapter = _SpyAdapter()
    listener = _listener(tmp_path, adapter)
    await listener.start()
    try:
        mode = stat.S_IMODE(os.stat(tmp_path / "dev-ingress.sock").st_mode)
    finally:
        await listener.stop()

    assert mode == 0o600, f"world-reachable dev ingress: {oct(mode)}"


async def test_it_never_binds_the_core_socket(tmp_path: Path) -> None:
    """The mistake this facility replaces. The gateway binds core.sock and the
    core dials IN, so a second connector is taken for the core reattaching."""
    from stackowl.paths import StackowlHome

    assert StackowlHome.dev_ingress_socket() != StackowlHome.core_socket()


async def test_a_stale_socket_file_does_not_block_startup(tmp_path: Path) -> None:
    """A killed process leaves the file behind and bind() then fails EADDRINUSE
    even though nothing is listening — a dev facility that needs a manual rm
    after every crash would not get used."""
    path = tmp_path / "dev-ingress.sock"
    path.write_text("stale")
    adapter = _SpyAdapter()
    listener = DevIngressListener(path, {"telegram": DevIngressTarget(
        adapter=adapter, is_allowed=lambda k: k == ALLOWED)})

    await listener.start()
    try:
        reply = await _request(path, {
            "channel": "telegram", "session_key": ALLOWED, "text": "still works",
        })
    finally:
        await listener.stop()
    assert reply["ok"] is True


async def test_malformed_input_never_kills_the_listener(tmp_path: Path) -> None:
    """B5. A bad line answers and the connection stays usable."""
    adapter = _SpyAdapter()
    listener = _listener(tmp_path, adapter)
    await listener.start()
    path = tmp_path / "dev-ingress.sock"
    try:
        bad = await _request(path, {"channel": "telegram", "session_key": ALLOWED})
        assert bad["ok"] is False, "empty text must be refused"
        good = await _request(path, {
            "channel": "telegram", "session_key": ALLOWED, "text": "after the bad one",
        })
        assert good["ok"] is True
    finally:
        await listener.stop()


async def test_a_json_true_chat_id_does_not_become_chat_1(tmp_path: Path) -> None:
    """`bool` is a subclass of `int` in Python, so a naive isinstance check turns
    `true` into chat_id 1 and delivers the reply to whatever chat is numbered 1."""
    adapter = _SpyAdapter()
    listener = _listener(tmp_path, adapter)
    await listener.start()
    try:
        await _request(tmp_path / "dev-ingress.sock", {
            "channel": "telegram", "session_key": ALLOWED,
            "text": "check the chat id", "chat_id": True,
        })
    finally:
        await listener.stop()

    assert adapter.fed[0].chat_id is None
