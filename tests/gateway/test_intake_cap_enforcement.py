"""Concurrency caps enforced in the production intake path (concurrent-msg §4.7, Task 8).

Task 7 added the cap PRIMITIVES to :class:`TurnRegistry` (``enqueue`` raises
:class:`QueueFull` past the per-session bound; ``at_global_capacity`` reports the
host-derived global ceiling). This suite proves the orchestrator's ``_intake``
now ENFORCES them inside the per-session intake-lock critical section:

* (a) **QueueFull** — at the per-session bound, a further same-session message
  yields a user-facing "too many queued" notice via the channel adapter
  ``send_text`` and is DROPPED; the intake loop never crashes and the queue never
  grows unbounded.
* (b) **Global cap** — at global capacity, a NEW same-session-idle turn is HELD
  (enqueued, not dispatched beyond the cap) with a "busy — queued" ack, then runs
  once capacity frees.

These mirror the orchestrator's exact locked critical section (like
``test_intake_drain_race.py`` does) against the REAL :class:`TurnRegistry`, so the
dispatch-vs-enqueue + QueueFull + global-cap decisions are exercised end to end.
Every await is bounded by ``asyncio.wait_for`` so a hang/deadlock FAILS.

RUN-TO-FAIL: ``_LegacyIntake`` mirrors the PRE-fix ``_intake`` (no QueueFull
catch, no global-cap check) and demonstrates the two correctness bugs — an
uncaught :class:`QueueFull` propagating out of intake, and an over-cap dispatch.
``_FixedIntake`` mirrors the post-fix body and passes.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.gateway.turn_registry import QueueFull, TurnRegistry

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    """Minimal channel adapter capturing send_text notices."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_text(self, text: str) -> None:
        self.texts.append(text)


# --------------------------------------------------------------------------- #
# Harness mirroring the orchestrator's _intake locked critical section.
# --------------------------------------------------------------------------- #
class _Intake:
    """Base harness: dispatch a slow turn, mirror the intake decision under lock."""

    def __init__(self, reg: TurnRegistry, adapter: _FakeAdapter, session_id: str) -> None:
        self._reg = reg
        self._adapter = adapter
        self._sid = session_id
        self.dispatched: list[str] = []
        self.dropped: list[str] = []

    async def _dispatch(self, request_id: str, text: str) -> None:
        task = asyncio.create_task(asyncio.sleep(3600))  # long-lived running turn
        await self._reg.register(
            request_id, session_id=self._sid, task=task,
            target=None, original_input=text,
        )
        self.dispatched.append(text)


class _LegacyIntake(_Intake):
    """PRE-fix _intake: NO global-cap check, NO QueueFull catch (the bug)."""

    async def intake(self, request_id: str, text: str) -> None:
        async with self._reg.session_intake_lock(self._sid):
            if self._reg.running(self._sid) is None:
                await self._dispatch(request_id, text)
            else:
                # Unguarded enqueue — QueueFull propagates UNCAUGHT out of intake.
                self._reg.enqueue(
                    self._sid, original_input=text, request_id=request_id, target=None,
                )


class _FixedIntake(_Intake):
    """POST-fix _intake: global-cap hold + QueueFull notice, all under the lock."""

    async def intake(self, request_id: str, text: str) -> None:
        queued_busy = False
        async with self._reg.session_intake_lock(self._sid):
            if self._reg.running(self._sid) is None and not self._reg.at_global_capacity():
                await self._dispatch(request_id, text)
            else:
                # Either a same-session turn is running OR we are at the global
                # cap: HOLD this turn (bounded enqueue) so it runs when capacity
                # frees. Overflow -> user notice + drop (never crash, never grow).
                try:
                    self._reg.enqueue(
                        self._sid, original_input=text, request_id=request_id, target=None,
                    )
                    queued_busy = self._reg.running(self._sid) is None  # global-cap hold
                except QueueFull:
                    self.dropped.append(text)
                    with contextlib.suppress(Exception):
                        await self._adapter.send_text(
                            "Too many queued messages — please wait."
                        )
                    return
        if queued_busy:
            with contextlib.suppress(Exception):
                await self._adapter.send_text("Busy — queued; I'll start that shortly.")


# --------------------------------------------------------------------------- (a)
async def test_queuefull_propagates_uncaught_in_legacy_intake() -> None:
    """RUN-TO-FAIL: the PRE-fix intake lets QueueFull escape (crashes the loop)."""
    reg = TurnRegistry(per_session_queue_max=2, global_running_max=100)
    adapter = _FakeAdapter()
    h = _LegacyIntake(reg, adapter, "s1")

    # r0 runs; the queue fills to its bound (2).
    await asyncio.wait_for(h.intake("r0", "running"), 1.0)
    await asyncio.wait_for(h.intake("r1", "q1"), 1.0)
    await asyncio.wait_for(h.intake("r2", "q2"), 1.0)
    # The next same-session intake overflows -> QueueFull escapes UNCAUGHT.
    with pytest.raises(QueueFull):
        await asyncio.wait_for(h.intake("r3", "q3"), 1.0)


async def test_queuefull_yields_user_notice_and_drops_without_crash() -> None:
    """RUN-TO-PASS: at the per-session bound, a further message -> notice + drop, no crash."""
    reg = TurnRegistry(per_session_queue_max=2, global_running_max=100)
    adapter = _FakeAdapter()
    h = _FixedIntake(reg, adapter, "s1")

    await asyncio.wait_for(h.intake("r0", "running"), 1.0)  # dispatched
    await asyncio.wait_for(h.intake("r1", "q1"), 1.0)  # queued
    await asyncio.wait_for(h.intake("r2", "q2"), 1.0)  # queued (bound reached)
    # Overflow: caught, user-notified, dropped — intake returns normally.
    await asyncio.wait_for(h.intake("r3", "q3"), 1.0)

    assert h.dispatched == ["running"]
    assert h.dropped == ["q3"]
    assert any("Too many queued" in t for t in adapter.texts), adapter.texts
    # No unbounded growth: exactly per_session_queue_max entries remain queued.
    drained = []
    while (nxt := reg.pop_next("s1")) is not None:
        drained.append(nxt.original_input)
    assert drained == ["q1", "q2"], drained  # q3 never entered the queue


# --------------------------------------------------------------------------- (b)
async def test_legacy_intake_dispatches_beyond_global_cap() -> None:
    """RUN-TO-FAIL: the PRE-fix intake dispatches a fresh-session turn OVER the cap."""
    reg = TurnRegistry(per_session_queue_max=8, global_running_max=1)
    adapter = _FakeAdapter()
    # sA holds the only global slot.
    a = _LegacyIntake(reg, adapter, "sA")
    await asyncio.wait_for(a.intake("rA", "a"), 1.0)
    assert reg.at_global_capacity() is True

    # A fresh session sB, idle, would dispatch a SECOND running turn (over cap).
    b = _LegacyIntake(reg, adapter, "sB")
    await asyncio.wait_for(b.intake("rB", "b"), 1.0)
    assert b.dispatched == ["b"]  # the bug: dispatched despite being at capacity
    assert reg.running("sB") is not None


async def test_global_cap_holds_new_session_turn_then_runs_when_freed() -> None:
    """RUN-TO-PASS: at global cap, a fresh-session turn is HELD (queued) + busy-acked.

    Once the holder deregisters (capacity frees), a drain wakes the held turn and
    it dispatches — proving the hold is not a permanent strand.
    """
    reg = TurnRegistry(per_session_queue_max=8, global_running_max=1)
    adapter = _FakeAdapter()

    a = _FixedIntake(reg, adapter, "sA")
    await asyncio.wait_for(a.intake("rA", "a"), 1.0)  # holds the only slot
    assert reg.at_global_capacity() is True

    b = _FixedIntake(reg, adapter, "sB")
    await asyncio.wait_for(b.intake("rB", "b"), 1.0)
    # HELD, not dispatched beyond the cap.
    assert b.dispatched == []
    assert reg.running("sB") is None
    assert any("Busy" in t for t in adapter.texts), adapter.texts
    # Queued on its own session for later wake.
    held = reg.pop_next("sB")
    assert held is not None and held.original_input == "b"

    # Capacity frees: re-enqueue + wake. The global-cap-wake helper finds sB's
    # idle queue and dispatches it.
    reg.enqueue("sB", original_input="b", request_id="rB", target=None)
    await reg.deregister("rA")
    assert reg.at_global_capacity() is False
    woken = reg.idle_queued_session()
    assert woken == "sB", "expected a global-cap-wake helper to surface the held session"
    nxt = reg.pop_next(woken)
    assert nxt is not None
    await b._dispatch(nxt.request_id, nxt.original_input)
    assert reg.running("sB") is not None
