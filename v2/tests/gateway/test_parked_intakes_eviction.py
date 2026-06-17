"""STEER-3 (F057) — parked intakes are reclaimed on the failure / lost-drain path.

F057: ``_parked_intakes[request_id] = msg`` is set on the busy/global-cap hold
path and the routed-NEW enqueue path, but entries are popped ONLY when the
matching queue item is later drained. If the drain task is GC'd, or a session
wedges, the parked entry is never popped — a slow leak of raw IngressMessages.

The fix: the ``ParkedIntakes`` map evicts entries whose ``request_id`` (or a
``{request_id}-survivor-N`` key derived from it) is reaped by the turn sweep.
``TurnRegistry.sweep`` fires an ``on_reaped(reaped_rids)`` callback (mirroring the
existing ``on_stranded`` hook); the orchestrator wires it to ``ParkedIntakes.evict``
so a wedged/GC'd session's parked entries cannot grow without bound.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.parked_intakes import ParkedIntakes
from stackowl.gateway.scanner import IngressMessage
from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus


def _msg(rid: str) -> IngressMessage:
    return IngressMessage(text="hi", session_id="s1", channel="cli", trace_id=rid)


def test_evict_removes_reaped_request_id() -> None:
    parked = ParkedIntakes()
    parked.put("r1", _msg("r1"))
    parked.put("r2", _msg("r2"))
    evicted = parked.evict(["r1"])
    assert evicted == 1
    assert parked.get_and_pop("r1") is None
    assert parked.get_and_pop("r2") is not None  # untouched


def test_evict_removes_survivor_derived_keys() -> None:
    parked = ParkedIntakes()
    # finalize_and_drain parks survivors under `{rid}-survivor-N`.
    parked.put("r1-survivor-0", _msg("r1-survivor-0"))
    parked.put("r1-survivor-1", _msg("r1-survivor-1"))
    parked.put("other", _msg("other"))
    # Reaping the PARENT r1 must also reclaim its survivor-derived parked entries.
    evicted = parked.evict(["r1"])
    assert evicted == 2
    assert parked.get_and_pop("r1-survivor-0") is None
    assert parked.get_and_pop("r1-survivor-1") is None
    assert parked.get_and_pop("other") is not None


def test_get_and_pop_is_one_shot() -> None:
    parked = ParkedIntakes()
    parked.put("r1", _msg("r1"))
    assert parked.get_and_pop("r1") is not None
    assert parked.get_and_pop("r1") is None  # popped — gone
    assert len(parked) == 0


@pytest.mark.asyncio
async def test_sweep_fires_on_reaped_and_evicts_parked() -> None:
    """A wedged session's reap evicts its parked intake — no unbounded growth."""
    reg = TurnRegistry()
    parked = ParkedIntakes()
    reg.set_reaped_evictor(parked.evict)

    # A wedged turn: task DONE but status never reached DONE (the F050 wedge),
    # plus a parked intake keyed by its request_id (the leak shape).
    async def _noop() -> None:
        return None

    task = asyncio.create_task(_noop())
    await task  # task is now done()
    turn = await reg.register(
        "wedged-1", session_id="s1", task=task, target=None, original_input="x"
    )
    assert turn.status is not TurnStatus.DONE
    parked.put("wedged-1", _msg("wedged-1"))
    assert len(parked) == 1

    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "wedged-1" in reaped
    # The sweep's on_reaped callback evicted the parked intake — the leak is closed.
    assert len(parked) == 0
    assert parked.get_and_pop("wedged-1") is None
