"""Per-session intake lock — the _drain_next await window race (concurrent-msg §4.3 review).

The detached ``_drain_next`` task (scheduled by the producer ``_on_done``
done-callback) runs this sequence on the shared :class:`TurnRegistry`::

    await deregister(finished_request_id)       # session looks IDLE
    nxt = pop_next(session_id)
    consumed, text = await resolve_or_rewrite()  # AWAITS the LLM classifier -> YIELDS
    await dispatch_turn(...)                      # re-register happens only HERE

During the ``await resolve_or_rewrite`` yield (which only awaits when a clarify
is pending + a classifier is configured), the gateway message loop resumes and a
fresh same-session message's ``_intake`` checks ``running(s1)`` -> ``None`` (drain
hasn't re-registered yet) and DISPATCHES + registers a turn. Then ``_drain_next``
resumes and ALSO registers -> **two running turns for s1**, violating the
≤1-running-per-session invariant (``register`` overwrites ``_running[s1]``,
orphaning one turn and corrupting FIFO/drain).

These tests reproduce the EXACT interleaving by mirroring the two orchestrator
critical sections (the dispatch-vs-enqueue decision in ``_intake`` and the
deregister->pop->resolve->dispatch sequence in ``_drain_next``) against the REAL
:class:`TurnRegistry`, with a controllable awaiting ``resolve_or_rewrite`` (an
:class:`asyncio.Event` the test releases) so the drain yields mid-decision.

The ``use_lock`` parameter drives the same interleaving with and without the
per-session intake lock: without it, two running turns appear (the bug); with it,
the fresh intake BLOCKS until drain has re-registered and instead ENQUEUES. Every
await is bounded by ``asyncio.wait_for`` so a hang/deadlock FAILS, never wedges.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.gateway.turn_registry import PendingIntake, TurnRegistry

pytestmark = pytest.mark.asyncio


class _RaceHarness:
    """Mirrors the orchestrator's _intake / _drain_next critical sections.

    ``resolve_or_rewrite`` awaits a test-controlled gate so the drain yields mid
    decision, exactly where the real classifier (``ClarifyIntentClassifier``)
    yields. ``use_lock`` toggles the per-session intake lock so the SAME
    interleaving runs against unfixed (no lock) and fixed (lock) behavior.
    """

    def __init__(self, reg: TurnRegistry, *, use_lock: bool) -> None:
        self._reg = reg
        self._use_lock = use_lock
        # Tripped if at any observed instant >1 turn is RUNNING for s1.
        self.max_concurrent_running = 0
        # The awaiting-resolve gate (drain blocks here until the test releases).
        self.resolve_gate = asyncio.Event()
        self.resolve_entered = asyncio.Event()
        self.dispatched: list[str] = []

    def _record_running(self) -> None:
        # _running maps session->request_id; a single session can only ever map
        # to one id, so "two running turns" manifests as a register OVERWRITING a
        # live session slot. Count live turns whose session is s1 via the queues
        # is not enough — assert on the running-slot invariant directly below.
        running = 1 if self._reg.running("s1") is not None else 0
        self.max_concurrent_running = max(self.max_concurrent_running, running)

    async def _resolve_or_rewrite(self) -> tuple[bool, str]:
        """Stand-in for ClarifyPump.resolve_or_rewrite that AWAITS (yields)."""
        self.resolve_entered.set()
        await self.resolve_gate.wait()  # the classifier yield window
        return False, "drained"

    async def dispatch(self, request_id: str, original_input: str) -> None:
        """Mirror _dispatch_turn: create a slow task, register the running slot."""
        task = asyncio.create_task(asyncio.sleep(0.05))
        await self._reg.register(
            request_id, session_id="s1", task=task,
            target=None, original_input=original_input,
        )
        self.dispatched.append(original_input)

    async def intake(self, request_id: str, original_input: str) -> None:
        """Mirror _intake: under the lock, dispatch if idle else enqueue."""
        lock = self._reg.session_intake_lock("s1")
        if self._use_lock:
            await lock.acquire()
        try:
            self._record_running()
            if self._reg.running("s1") is None:
                await self.dispatch(request_id, original_input)
            else:
                self._reg.enqueue(
                    "s1", original_input=original_input,
                    request_id=request_id, target=None,
                )
        finally:
            if self._use_lock:
                lock.release()

    async def drain_next(self, finished_request_id: str) -> None:
        """Mirror _drain_next: deregister -> pop -> AWAIT resolve -> dispatch.

        Under the lock the WHOLE sequence is held (spanning the resolve await),
        so a concurrent intake blocks until re-register.
        """
        lock = self._reg.session_intake_lock("s1")
        if self._use_lock:
            await lock.acquire()
        try:
            await self._reg.deregister(finished_request_id)
            nxt: PendingIntake | None = self._reg.pop_next("s1")
            if nxt is None:
                return
            consumed, text = await self._resolve_or_rewrite()
            if consumed:
                return
            self._record_running()
            await self.dispatch(nxt.request_id, text)
        finally:
            if self._use_lock:
                lock.release()


async def _run_interleaving(*, use_lock: bool) -> _RaceHarness:
    """Drive the exact race: drain blocks in resolve while a fresh intake fires."""
    reg = TurnRegistry()
    h = _RaceHarness(reg, use_lock=use_lock)

    # req-1 is the running turn; req-2 is already queued behind it (the message
    # the drain will pop + resolve).
    t1 = asyncio.create_task(asyncio.sleep(0.05))
    await reg.register("req-1", session_id="s1", task=t1, target=None, original_input="first")
    reg.enqueue("s1", original_input="second", request_id="req-2", target=None)

    # req-1 completed -> drain starts. It deregisters (session now looks idle),
    # pops req-2, then BLOCKS in resolve_or_rewrite.
    drain = asyncio.create_task(h.drain_next("req-1"))
    await asyncio.wait_for(h.resolve_entered.wait(), 1.0)

    # While drain is parked in resolve, a fresh same-session message arrives.
    # WITHOUT the lock: intake sees running(s1) is None -> dispatches req-3 NOW
    # (a SECOND running turn). WITH the lock: intake blocks until drain releases.
    intake = asyncio.create_task(h.intake("req-3", "third"))

    # Give the intake a chance to run (it must block under the lock; it must
    # dispatch immediately without it).
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Release the drain's resolve await; let everything settle.
    h.resolve_gate.set()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.gather(drain, intake), 2.0)
    # Ensure both finished (no deadlock).
    await asyncio.wait_for(drain, 1.0)
    await asyncio.wait_for(intake, 1.0)
    return h


async def test_drain_resolve_window_allows_second_running_turn_without_lock() -> None:
    """RUN-TO-FAIL evidence: without the lock, a fresh intake starts a 2nd turn.

    The fresh ``_intake`` for req-3 dispatches a SECOND running turn while the
    detached drain is parked in ``resolve_or_rewrite`` — both then register on
    s1, so ``dispatched`` contains BOTH req-3's input and the drained req-2 input
    (two dispatches where the invariant permits at most one new running turn at a
    time). This is the bug the lock fixes.
    """
    h = await _run_interleaving(use_lock=False)
    # The bug: the fresh intake dispatched a turn (instead of enqueuing) WHILE the
    # drain was mid-decision, so BOTH paths dispatched -> two running turns for s1.
    assert "third" in h.dispatched, "expected the unfixed fresh intake to dispatch"
    assert "drained" in h.dispatched, "expected the drain to also dispatch"
    assert len(h.dispatched) == 2, (
        "unfixed: both _intake and _drain_next dispatched a running turn for s1 "
        f"(double-run), got dispatched={h.dispatched}"
    )


async def test_per_session_lock_serializes_intake_and_drain() -> None:
    """RUN-TO-PASS: with the lock, the fresh intake ENQUEUES (never a 2nd turn).

    The lock makes intake block until the drain has re-registered; intake then
    sees the session RUNNING and enqueues req-3 instead of dispatching it. At no
    observed instant are there two running turns. After settling, exactly one
    turn (the drained req-2) ran and req-3 is queued behind it (FIFO preserved).
    """
    h = await _run_interleaving(use_lock=True)
    assert h.max_concurrent_running <= 1, (
        "two running turns observed for s1 despite the per-session intake lock"
    )
    # Only the drained message dispatched; the fresh intake enqueued.
    assert h.dispatched == ["drained"], (
        f"the fresh intake should have enqueued, not dispatched; got {h.dispatched}"
    )
    # req-3 sits in the FIFO queue behind the now-running drained turn.
    nxt = h._reg.pop_next("s1")
    assert nxt is not None and nxt.original_input == "third", (
        "fresh intake should be queued FIFO behind the drained turn"
    )


async def test_session_intake_lock_is_stable_and_per_session() -> None:
    """The lock is the SAME object per session and DISTINCT across sessions."""
    reg = TurnRegistry()
    a1 = reg.session_intake_lock("sA")
    a2 = reg.session_intake_lock("sA")
    b1 = reg.session_intake_lock("sB")
    assert a1 is a2  # stable per session
    assert a1 is not b1  # different sessions -> different locks (cross-session free)
