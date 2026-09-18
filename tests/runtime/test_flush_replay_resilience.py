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
        text=text, session_key="s1", channel="cli", trace_id="t-" + text, chat_id=None
    )


#: Spec 2.4 — fixed per-file secret; both GatewayLink( constructions AND every
#: HelloFrame fed through `_route` below use this same value (a real/matching
#: core Hello — these tests exercise the REPLAY path, not the secret check).
_LINK_SECRET = "test-link-secret"


def _hello(pid: int = 1) -> HelloFrame:
    return HelloFrame(
        sender_pid=pid, highest_migration=1, registry_digest="x", link_secret=_LINK_SECRET,
    )


async def test_transient_replay_failure_is_requeued_not_dropped() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter}, link_secret=_LINK_SECRET)

    # No connection yet -> the turn buffers.
    await link.submit(_msg("x"))
    assert [m.text for m in link._pending] == ["x"]

    # A fresh core whose send faults the moment we replay.
    link.set_connection(_FailingConn(), local_hello=_hello())  # type: ignore[arg-type]
    await link._route(_hello())

    # Re-queued for retry, NOT silently dropped, and the user is not yet bothered.
    assert [m.text for m in link._pending] == ["x"]
    assert adapter.texts == []


async def test_replay_recovers_when_a_later_core_accepts() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter}, link_secret=_LINK_SECRET)
    await link.submit(_msg("x"))

    # First fresh core faults -> re-queued.
    link.set_connection(_FailingConn(), local_hello=_hello())  # type: ignore[arg-type]
    await link._route(_hello())
    assert [m.text for m in link._pending] == ["x"]

    # A healthy core then accepts -> the buffered turn is delivered, no notice.
    ok = _OkConn()
    link.set_connection(ok, local_hello=_hello())  # type: ignore[arg-type]
    await link._route(_hello(2))

    forwarded = [f for f in ok.sent if isinstance(f, IngressFrame)]
    assert [f.text for f in forwarded] == ["x"]
    assert forwarded[0].trace_id == "t-x"
    assert link._pending == []
    assert adapter.texts == []


async def test_exhausted_replay_notifies_originating_adapter() -> None:
    adapter = _FakeAdapter()
    link = GatewayLink({"cli": adapter}, link_secret=_LINK_SECRET)
    await link.submit(_msg("x"))

    # Each Hello replays once against a perpetually-faulting core. After the
    # bounded number of attempts the turn is surfaced, not suppressed.
    bad = _FailingConn()
    for _ in range(GatewayLink._MAX_REPLAY_ATTEMPTS):
        link.set_connection(bad, local_hello=_hello())  # type: ignore[arg-type]
        await link._route(_hello())

    assert link._pending == []
    assert adapter.texts == [_REPLAY_FAILURE_NOTICE]
    # The turn is no longer tracked as in-flight (no later crash can resurrect it).
    assert "t-x" not in link._inflight
