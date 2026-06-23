"""Consent over the gateway/core socket — full round-trip on a real socket.

Proves the split consent path: the CORE's SocketConsentPrompter sends a
ConsentRequestFrame, the GATEWAY's GatewayLink resolves it through the real
RoutingPrompter (here a fake), and the decision returns as a ConsentResponseFrame
that resolves the core's blocked prompt(). Fail-closed on no response.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.ipc.client import IpcClient
from stackowl.ipc.connection import FrameConnection
from stackowl.ipc.frames import ConsentResponseFrame
from stackowl.ipc.server import IpcServer
from stackowl.runtime.gateway_link import GatewayLink
from stackowl.runtime.socket_consent import SocketConsentPrompter
from stackowl.tools.consent import ConsentRequest, ConsentScope


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / "core.sock"


class _FakeRouter:
    def __init__(self, scope: ConsentScope) -> None:
        self.scope = scope
        self.seen: list[ConsentRequest] = []

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.seen.append(req)
        return self.scope


async def _wire(socket_path, router):
    link = GatewayLink({"telegram": _NullAdapter()}, consent_router=router)

    async def gw_accept(conn: FrameConnection) -> None:
        link.set_connection(conn)
        await link.run(conn)

    server = IpcServer(socket_path)
    await server.start(gw_accept)
    core_conn = await IpcClient(socket_path).connect(timeout_s=5)
    prompter = SocketConsentPrompter(core_conn, timeout_seconds=5)

    async def core_reader() -> None:
        async for frame in core_conn:
            if isinstance(frame, ConsentResponseFrame):
                prompter.resolve(frame.consent_id, frame.scope)

    reader_task = asyncio.create_task(core_reader())

    async def stop() -> None:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader_task
        await core_conn.aclose()
        await server.stop()

    return prompter, link, stop


class _NullAdapter:
    channel_name = "telegram"

    async def send(self, reader) -> None:  # noqa: ANN001
        return None

    async def send_text(self, text: str) -> None:
        return None


async def test_consent_granted_round_trip(socket_path) -> None:
    router = _FakeRouter(ConsentScope.SESSION)
    prompter, _link, stop = await _wire(socket_path, router)
    try:
        req = ConsentRequest(
            tool_name="shell", channel="telegram", session_id="123", summary="run ls"
        )
        scope = await asyncio.wait_for(prompter.prompt(req), timeout=5)
    finally:
        await stop()

    assert scope == ConsentScope.SESSION
    assert router.seen and router.seen[0].tool_name == "shell"
    assert router.seen[0].channel == "telegram"


async def test_consent_denied_round_trip(socket_path) -> None:
    router = _FakeRouter(ConsentScope.DENY)
    prompter, _link, stop = await _wire(socket_path, router)
    try:
        req = ConsentRequest(tool_name="shell", channel="telegram", session_id="9")
        scope = await asyncio.wait_for(prompter.prompt(req), timeout=5)
    finally:
        await stop()
    assert scope == ConsentScope.DENY


async def test_consent_fails_closed_when_router_missing(socket_path) -> None:
    # No consent_router on the gateway -> DENY returned (never granted by default).
    prompter, _link, stop = await _wire(socket_path, None)
    try:
        req = ConsentRequest(tool_name="shell", channel="telegram", session_id="9")
        scope = await asyncio.wait_for(prompter.prompt(req), timeout=5)
    finally:
        await stop()
    assert scope == ConsentScope.DENY
