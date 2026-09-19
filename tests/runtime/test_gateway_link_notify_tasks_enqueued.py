"""``GatewayLink.notify_tasks_enqueued`` — the gateway-role half of Story 4.3's
"a declared command runs through one door" (AD-1). A gateway-role caller of
``commands/spec/submit.py::submit_command(..., run_inline=False,
notify_enqueued=link.notify_tasks_enqueued)`` has no in-process ``TaskLoop``
to claim against; this is what tells core to wake its own loop instead of
waiting out the tick.
"""

from __future__ import annotations

from stackowl.ipc.frames import TasksEnqueuedFrame
from stackowl.runtime.gateway_link import GatewayLink

_LINK_SECRET = "test-link-secret"


class _RecordingConn:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, frame: object) -> None:
        self.sent.append(frame)


class _RaisingConn:
    async def send(self, frame: object) -> None:
        raise RuntimeError("connection reset")


def _hello():  # type: ignore[no-untyped-def]
    from stackowl.ipc.frames import HelloFrame

    return HelloFrame(sender_pid=1, highest_migration=1, registry_digest="x")


async def test_notify_sends_a_payload_free_tasks_enqueued_frame() -> None:
    conn = _RecordingConn()
    link = GatewayLink({}, link_secret=_LINK_SECRET)
    link.set_connection(conn, local_hello=_hello())  # type: ignore[arg-type]

    await link.notify_tasks_enqueued()

    assert len(conn.sent) == 1
    assert isinstance(conn.sent[0], TasksEnqueuedFrame)


async def test_notify_with_no_connection_never_raises() -> None:
    link = GatewayLink({}, link_secret=_LINK_SECRET)
    # No set_connection() call — the buffering-between-core-restarts case.
    await link.notify_tasks_enqueued()  # must not raise


async def test_notify_swallows_a_send_failure() -> None:
    link = GatewayLink({}, link_secret=_LINK_SECRET)
    link.set_connection(_RaisingConn(), local_hello=_hello())  # type: ignore[arg-type]

    await link.notify_tasks_enqueued()  # must not raise — best-effort
