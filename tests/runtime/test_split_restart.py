"""Phase 3/4/5 — quiesce drain, GatewayLink reconnect/buffer, restart wiring.

Unit-level guards for the restart machinery: the drain ceiling, the gateway
link buffering inbound across a core exec-replace (and finalizing cut turns),
and the orchestrator's role guards. The full execv round-trip is a live-only
smoke (documented in the plan); these pin the seams that decide its behaviour.
"""

from __future__ import annotations

import asyncio

from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.frames import ChunkFrame, HelloFrame, IngressFrame, RestartNoticeFrame
from stackowl.runtime.drain import quiesce
from stackowl.runtime.gateway_link import GatewayLink


class _FakeRegistry:
    """Drains a preset number of active turns over successive polls."""

    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self._i = 0

    def _cur(self) -> int:
        return self._counts[min(self._i, len(self._counts) - 1)]

    def has_active_turns(self) -> bool:
        v = self._cur() > 0
        self._i += 1
        return v

    def active_turn_count(self) -> int:
        return self._cur()


async def test_quiesce_returns_true_when_idle() -> None:
    assert await quiesce(_FakeRegistry([0]), grace_seconds=1.0) is True


async def test_quiesce_drains_then_returns_true() -> None:
    reg = _FakeRegistry([2, 1, 0])
    assert await quiesce(reg, grace_seconds=5.0, poll_interval_s=0.001) is True


async def test_quiesce_grace_ceiling_returns_false() -> None:
    # Never drains; the ceiling must be hit and reported (abandons stragglers).
    reg = _FakeRegistry([3])
    assert await quiesce(reg, grace_seconds=0.05, poll_interval_s=0.01) is False


# --- GatewayLink reconnection / buffering --------------------------------


class _FakeConn:
    """Records frames sent; never yields inbound (run() iterates an empty queue)."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, frame: object) -> None:
        self.sent.append(frame)


class _FakeAdapter:
    channel_name = "cli"

    def __init__(self) -> None:
        self.sent_streams = 0
        self.texts: list[str] = []

    async def send(self, reader) -> None:  # noqa: ANN001
        self.sent_streams += 1

    async def send_text(self, text: str) -> None:
        self.texts.append(text)


def _msg(text: str = "hi") -> IngressMessage:
    return IngressMessage(
        text=text, session_id="s1", channel="cli", trace_id="t-" + text, chat_id=None
    )


async def test_submit_buffers_when_no_connection() -> None:
    link = GatewayLink({"cli": _FakeAdapter()})
    # No connection bound yet -> the message is held, nothing is sent.
    await link.submit(_msg("a"))
    assert link._pending and link._pending[0].text == "a"


async def test_hello_flushes_buffered_messages() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    conn = _FakeConn()

    # Buffer while disconnected, then connect + receive a Hello -> flush.
    await link.submit(_msg("a"))
    await link.submit(_msg("b"))
    link.set_connection(conn)  # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=1))
    await asyncio.sleep(0.01)  # let the spawned adapter.send tasks run

    assert not link._pending
    # Both buffered messages were forwarded as IngressFrames.
    ingress = [f for f in conn.sent if isinstance(f, IngressFrame)]
    assert [f.text for f in ingress] == ["a", "b"]
    assert adapter.sent_streams == 2


async def test_restart_notice_starts_buffering() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    conn = _FakeConn()
    link.set_connection(conn)  # type: ignore[arg-type]

    # A restart notice means the core is tearing down: subsequent submits buffer.
    await link._route(RestartNoticeFrame(reason="code-change"))
    await link.submit(_msg("during-gap"))
    assert link._pending and link._pending[0].text == "during-gap"
    assert not [f for f in conn.sent if isinstance(f, IngressFrame)]


async def test_finalize_ends_cut_turn_readers() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    conn = _FakeConn()
    link.set_connection(conn)  # type: ignore[arg-type]

    # Submit opens a demux reader the adapter consumes; the chunk never closes.
    await link.submit(_msg("open"))
    reader = link._demux.register  # sanity: demux is in play
    assert reader is not None
    # A cut (core died) must finalize the reader so adapter.send completes.
    drained = asyncio.Event()

    async def _consume() -> None:
        # The reader handed to the adapter is internal; assert finalize clears it.
        await link.finalize()
        drained.set()

    await _consume()
    assert drained.is_set()
    # After finalize the demux holds no open turns.
    assert not link._demux._registry._writers


async def test_chunk_routes_to_demux_after_submit() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    conn = _FakeConn()
    link.set_connection(conn)  # type: ignore[arg-type]
    await link.submit(_msg("x"))
    # A chunk for the registered turn is accepted (no "unknown request" drop path).
    await link._route(
        ChunkFrame(content="hi", is_final=True, chunk_index=-1, trace_id="t-x", owl_name="")
    )
    # is_final cleaned the turn out of the demux.
    assert "t-x" not in link._demux._registry._writers
