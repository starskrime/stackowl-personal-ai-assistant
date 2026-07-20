"""GatewayLink must never silently lose a real channel-delivery failure.

Incident (2026-07-19): a Telegram flood-control ban made ``adapter.send`` raise
``RetryAfter`` for a real user turn. The spawned send task's done-callback only
called ``self._send_tasks.discard`` — it never inspected ``task.exception()`` —
so the failure was logged once by the adapter itself and then vanished: no
ERROR from the gateway link, no Failure routed to the recovery authority, and
the user never learned their "hi" went unanswered. This pins the fix: the
send-task done-callback must log loudly and route a consequential Failure to
RecoveryActuator, mirroring ``ClarifyPump._cleanup`` on the core side.
"""

from __future__ import annotations

import asyncio

from stackowl.gateway.scanner import IngressMessage
from stackowl.pipeline.recovery_actuator import Failure
from stackowl.runtime.gateway_link import GatewayLink


class _WorkingConn:
    async def send(self, frame: object) -> None:
        return None


class _RaisingAdapter:
    channel_name = "cli"

    async def send(self, reader) -> None:  # noqa: ANN001
        raise RuntimeError("simulated RetryAfter")

    async def send_text(self, text: str) -> None:
        return None


class _SpyActuator:
    def __init__(self) -> None:
        from stackowl.pipeline.recovery_actuator import RecoveryActuator

        self._real = RecoveryActuator()
        self.calls: list[object] = []

    async def recover(self, failure: object) -> object:
        self.calls.append(failure)
        return await self._real.recover(failure)  # type: ignore[arg-type]


def _msg() -> IngressMessage:
    return IngressMessage(
        text="hi", session_id="s1", channel="cli", trace_id="t-hi", chat_id=None
    )


async def test_send_task_failure_is_logged_and_routed_to_recovery() -> None:
    adapter = _RaisingAdapter()
    spy = _SpyActuator()
    link = GatewayLink({"cli": adapter}, recovery=spy)  # type: ignore[arg-type]
    link.set_connection(_WorkingConn())  # type: ignore[arg-type]

    await link.submit(_msg())
    # Let the send task fail, its done-callback fire, and the recovery task
    # it schedules actually run — each is its own loop hop.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(spy.calls) == 1
    failure = spy.calls[0]
    assert isinstance(failure, Failure)
    assert failure.kind == "send_task"
    assert failure.consequential is True
    assert "simulated RetryAfter" in failure.error


async def test_send_task_success_never_touches_recovery() -> None:
    class _OkAdapter:
        channel_name = "cli"

        async def send(self, reader) -> None:  # noqa: ANN001
            return None

        async def send_text(self, text: str) -> None:
            return None

    adapter = _OkAdapter()
    spy = _SpyActuator()
    link = GatewayLink({"cli": adapter}, recovery=spy)  # type: ignore[arg-type]
    link.set_connection(_WorkingConn())  # type: ignore[arg-type]

    await link.submit(_msg())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert spy.calls == []
