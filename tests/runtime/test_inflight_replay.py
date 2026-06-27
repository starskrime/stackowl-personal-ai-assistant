"""F-35 — a submitted-but-unfinished turn is replayed on reconnect, not lost.

``submit`` only buffers messages that arrive WHILE disconnected/buffering. A turn
already forwarded via ``_do_submit`` (the common case — the core was alive) is
tracked nowhere. If the core then crashes mid-turn, ``finalize`` ends the cut
reader (stops the spinner) but the objective is forgotten — the user must re-ask.

Fix: track submitted-but-unfinished turns in an in-flight map keyed by the
existing ``trace_id`` (the request_id used for demux routing + core idempotency).
On ``finalize`` (the crash path) move any still-in-flight message back into
``_pending`` so the next ``Hello`` replays it; a normally-finished turn (its
stream closed with ``is_final``) is removed from the in-flight map and is NOT
replayed. Replay reuses the SAME trace_id so the core dedupes a double-execute.
"""

from __future__ import annotations

import asyncio

from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.frames import ChunkFrame, HelloFrame, IngressFrame
from stackowl.runtime.gateway_link import GatewayLink


class _FakeConn:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, frame: object) -> None:
        self.sent.append(frame)


class _FakeAdapter:
    channel_name = "cli"

    def __init__(self) -> None:
        self.sent_streams = 0

    async def send(self, reader) -> None:  # noqa: ANN001
        self.sent_streams += 1

    async def send_text(self, text: str) -> None:  # pragma: no cover - unused
        pass


def _msg(text: str) -> IngressMessage:
    return IngressMessage(
        text=text, session_id="s1", channel="cli", trace_id="t-" + text, chat_id=None
    )


async def test_unfinished_turn_is_requeued_on_crash_and_replayed() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    conn = _FakeConn()
    link.set_connection(conn)  # type: ignore[arg-type]

    # A turn forwarded to a live core (the common case). No is_final arrives ->
    # the core crashes mid-turn.
    await link.submit(_msg("inflight"))
    assert any(isinstance(f, IngressFrame) for f in conn.sent)

    # Crash path: the gateway drops the connection and finalizes cut readers.
    link.drop_connection()
    await link.finalize()

    # The unfinished turn is back in _pending (not lost), keyed identically.
    assert [m.text for m in link._pending] == ["inflight"]
    assert link._pending[0].trace_id == "t-inflight"

    # A fresh core reconnects + says Hello -> the goal is replayed with the SAME
    # trace_id (idempotent re-execute), so it is not silently forgotten.
    conn2 = _FakeConn()
    link.set_connection(conn2)  # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=2))
    await asyncio.sleep(0.01)

    replayed = [f for f in conn2.sent if isinstance(f, IngressFrame)]
    assert [f.text for f in replayed] == ["inflight"]
    assert replayed[0].trace_id == "t-inflight"
    assert not link._pending


async def test_finished_turn_is_not_replayed_after_a_later_crash() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    conn = _FakeConn()
    link.set_connection(conn)  # type: ignore[arg-type]

    await link.submit(_msg("done"))
    # The turn's stream closes normally (is_final) BEFORE any crash.
    await link._route(
        ChunkFrame(content="", is_final=True, chunk_index=-1, trace_id="t-done", owl_name="")
    )

    # A later crash must NOT resurrect the already-finished turn.
    link.drop_connection()
    await link.finalize()

    assert link._pending == []
