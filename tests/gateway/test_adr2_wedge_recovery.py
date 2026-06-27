"""ADR-2 — TurnRegistry's reaped-wedged-turn re-dispatch DECISION delegates to RecoveryActuator.

The wedge re-dispatch budget (F-67) already exists; ADR-2 routes the "may I re-dispatch this
reaped wedged turn?" decision through the one ``RecoveryActuator.should_retry`` authority
instead of the inline ``generation < _MAX_WEDGE_REDISPATCH`` guard. Byte-identical: a wedged
turn's goal is non-consequential and transient-by-policy, so the authority agrees with the
budget gate and the ``enqueue`` re-dispatch EXECUTION is unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

import stackowl.gateway.turn_registry as _tr_mod
from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus


async def _done_task() -> asyncio.Task[None]:
    async def _noop() -> None:
        return None

    t: asyncio.Task[None] = asyncio.create_task(_noop())
    await t
    return t


class _SpyActuator:
    def __init__(self) -> None:
        from stackowl.pipeline.recovery_actuator import RecoveryActuator

        self._real = RecoveryActuator()
        self.calls: list[object] = []

    def should_retry(self, failure: object) -> bool:
        self.calls.append(failure)
        return self._real.should_retry(failure)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_redispatch_decision_routes_through_actuator_when_unify_on(monkeypatch) -> None:  # noqa: ANN001
    from stackowl.pipeline.recovery_actuator import Failure

    monkeypatch.setattr(_tr_mod, "_unify_gateway_enabled", lambda: True)
    spy = _SpyActuator()
    reg = TurnRegistry(recovery=spy)  # type: ignore[arg-type]
    task = await _done_task()
    turn = await reg.register(
        "wedged-1", session_id="s1", task=task, target=99, original_input="do the thing"
    )
    assert turn.status is not TurnStatus.DONE

    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "wedged-1" in reaped

    # The authority decided the re-dispatch (delegation) ...
    assert len(spy.calls) == 1
    failure = spy.calls[0]
    assert isinstance(failure, Failure)
    assert failure.kind == "gateway_turn"
    assert failure.consequential is False
    # ... and byte-identically the goal is back on the queue.
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.original_input == "do the thing"


@pytest.mark.asyncio
async def test_redispatch_decision_inline_when_unify_off(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(_tr_mod, "_unify_gateway_enabled", lambda: False)
    spy = _SpyActuator()
    reg = TurnRegistry(recovery=spy)  # type: ignore[arg-type]
    task = await _done_task()
    await reg.register(
        "wedged-1", session_id="s1", task=task, target=99, original_input="do the thing"
    )

    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "wedged-1" in reaped

    assert spy.calls == []  # inline path — authority not consulted
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.original_input == "do the thing"  # same byte-identical re-dispatch
