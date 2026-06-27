"""ADR-2 — GatewayLink's buffered-turn replay retry DECISION delegates to RecoveryActuator.

The replay-retry budget (F-38) already exists; ADR-2 routes the "may I retry this failed
replay?" decision through the one ``RecoveryActuator.should_retry`` authority instead of the
inline ``attempts < _MAX_REPLAY_ATTEMPTS`` guard. Byte-identical: a lost in-flight turn is
non-consequential and transient-by-policy, so the authority agrees with the budget gate.
"""

from __future__ import annotations

import stackowl.runtime.gateway_link as _gl_mod
from stackowl.gateway.scanner import IngressMessage
from stackowl.ipc.frames import HelloFrame
from stackowl.runtime.gateway_link import GatewayLink


class _FailingConn:
    async def send(self, frame: object) -> None:
        raise ConnectionResetError("socket gone")


class _FakeAdapter:
    channel_name = "cli"

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send(self, reader) -> None:  # noqa: ANN001
        return None

    async def send_text(self, text: str) -> None:
        self.texts.append(text)


class _SpyActuator:
    def __init__(self) -> None:
        from stackowl.pipeline.recovery_actuator import RecoveryActuator

        self._real = RecoveryActuator()
        self.calls: list[object] = []

    def should_retry(self, failure: object) -> bool:
        self.calls.append(failure)
        return self._real.should_retry(failure)  # type: ignore[arg-type]


def _msg(text: str) -> IngressMessage:
    return IngressMessage(
        text=text, session_id="s1", channel="cli", trace_id="t-" + text, chat_id=None
    )


async def test_replay_decision_routes_through_actuator_when_unify_on(monkeypatch) -> None:  # noqa: ANN001
    from stackowl.pipeline.recovery_actuator import Failure

    monkeypatch.setattr(_gl_mod, "_unify_gateway_enabled", lambda: True)
    adapter = _FakeAdapter()
    spy = _SpyActuator()
    link = GatewayLink({"cli": adapter}, recovery=spy)  # type: ignore[arg-type]

    await link.submit(_msg("x"))            # buffers (no conn)
    link.set_connection(_FailingConn())     # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=1))  # replay fails → retry decision

    assert len(spy.calls) == 1
    failure = spy.calls[0]
    assert isinstance(failure, Failure)
    assert failure.kind == "gateway_turn"
    assert failure.consequential is False
    # Byte-identical: re-queued for retry, user not yet bothered.
    assert [m.text for m in link._pending] == ["x"]
    assert adapter.texts == []


async def test_replay_decision_inline_when_unify_off(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(_gl_mod, "_unify_gateway_enabled", lambda: False)
    adapter = _FakeAdapter()
    spy = _SpyActuator()
    link = GatewayLink({"cli": adapter}, recovery=spy)  # type: ignore[arg-type]

    await link.submit(_msg("x"))
    link.set_connection(_FailingConn())  # type: ignore[arg-type]
    await link._route(HelloFrame(core_pid=1))

    assert spy.calls == []  # inline path — authority not consulted
    assert [m.text for m in link._pending] == ["x"]  # same byte-identical re-queue
