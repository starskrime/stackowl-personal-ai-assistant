"""F-38 — a buffered turn whose replay fails is not silently dropped.

``_flush_pending`` replays messages buffered during a core restart. The old code
wrapped each replay in ``contextlib.suppress(Exception)``: a replay that raised
(e.g. the fresh core's socket faulted the instant it said Hello) vanished with no
log and no user signal. The fix re-queues a transient failure for the next
``Hello`` up to a bounded number of attempts, then — once exhausted — surfaces a
visible failure notice to the originating adapter instead of suppress-and-drop.
"""

from __future__ import annotations

from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.frames import HelloFrame, IngressFrame
from stackowl.runtime.gateway_link import _REPLAY_FAILURE_NOTICE, GatewayLink


class _FailingConn:
    """A core connection whose every send raises (a faulted fresh socket)."""

    def __init__(self) -> None:
        self.attempts = 0

    async def send(self, frame: object) -> None:
        self.attempts += 1
        raise ConnectionResetError("socket gone")


class _OkConn:
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


def _msg(text: str) -> IngressMessage:
    return IngressMessage(
        text=text, session_id="s1", channel="cli", trace_id="t-" + text, chat_id=None
    )


async def test_transient_replay_failure_is_requeued_not_dropped() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})

    # No connection yet -> the turn buffers.
    await link.submit(_msg("x"))
    assert [m.text for m in link._pending] == ["x"]

    # A fresh core whose send faults the moment we replay.
    link.set_connection(_FailingConn())  # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=1))

    # Re-queued for retry, NOT silently dropped, and the user is not yet bothered.
    assert [m.text for m in link._pending] == ["x"]
    assert adapter.texts == []


async def test_replay_recovers_when_a_later_core_accepts() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    await link.submit(_msg("x"))

    # First fresh core faults -> re-queued.
    link.set_connection(_FailingConn())  # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=1))
    assert [m.text for m in link._pending] == ["x"]

    # A healthy core then accepts -> the buffered turn is delivered, no notice.
    ok = _OkConn()
    link.set_connection(ok)  # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=2))

    forwarded = [f for f in ok.sent if isinstance(f, IngressFrame)]
    assert [f.text for f in forwarded] == ["x"]
    assert forwarded[0].trace_id == "t-x"
    assert link._pending == []
    assert adapter.texts == []


async def test_exhausted_replay_notifies_originating_adapter() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter})
    await link.submit(_msg("x"))

    # Each Hello replays once against a perpetually-faulting core. After the
    # bounded number of attempts the turn is surfaced, not suppressed.
    bad = _FailingConn()
    for _ in range(GatewayLink._MAX_REPLAY_ATTEMPTS):
        link.set_connection(bad)  # type: ignore[arg-type]
        await link._route(HelloFrame(core_pid=1))

    assert link._pending == []
    assert adapter.texts == [_REPLAY_FAILURE_NOTICE]
    # The turn is no longer tracked as in-flight (no later crash can resurrect it).
    assert "t-x" not in link._inflight
