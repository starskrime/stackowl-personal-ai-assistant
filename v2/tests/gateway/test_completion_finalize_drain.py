"""Lost-steer finalization — the completion-window race (concurrent-msg §9 inv.1).

The loop/completion HALF of the lost-steer invariant. Task 11 proved the
registry primitives (``try_steer`` / ``finalize_if_drained`` / ``drain_survivors``)
in isolation; this proves the COMPLETION SEAM that the orchestrator's
``_drain_next`` runs at turn teardown.

The window (the bug this fixes):

    provider loop ENDS         <- status is still RUNNING (loop just returned)
    [ ... window ... ]         <- a steer landing HERE is put_nowait'd onto the
                                  still-"RUNNING" turn whose loop is already over
    deregister(request_id)     <- the turn (and its mailbox) is GC'd -> steer LOST

The old completion seam called ``deregister`` DIRECTLY without first
transitioning RUNNING->FINALIZING and draining survivors. So a ``try_steer`` that
lands in that window reads RUNNING, ``put``s onto a mailbox whose loop will never
fold it, and the turn is then deregistered — a silently dropped instruction.

The fix wires ``finalize_and_drain(request_id)`` into the seam BEFORE
``deregister``: under the turn lock it transitions RUNNING->FINALIZING (so a
CONCURRENT ``try_steer`` now reads FINALIZING and converts to a queued-new turn)
THEN drains any mailbox survivors and re-routes each as a queued-new turn. The
FINALIZING flip and the drain are ATOMIC under one lock, so there is no instant
where status reads RUNNING but the drain already ran (which would re-open the
hole).

Outcome the test asserts, across many randomized interleavings: a steer racing
the completion teardown is NEVER lost — it is EITHER
  * converted to a queued-new turn by ``try_steer`` seeing FINALIZING, OR
  * accepted by the still-RUNNING turn then drained-as-a-survivor (queued-new),
never silently dropped onto a dead turn. Every await is bounded by
``asyncio.wait_for`` so a hang/deadlock FAILS rather than wedging.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus

pytestmark = pytest.mark.asyncio


async def _completion_teardown_unfixed(reg: TurnRegistry, request_id: str) -> list[str]:
    """The OLD seam: deregister directly, with NO finalize+drain.

    Mirrors the pre-fix ``_drain_next`` completion path. Returns the survivors it
    re-routed — always ``[]`` because it never drains. Used only to PROVE the
    steer is lost without the fix.
    """
    await reg.deregister(request_id)
    return []


async def _completion_teardown_fixed(reg: TurnRegistry, request_id: str) -> list[str]:
    """The FIXED seam: finalize_and_drain BEFORE deregister.

    Mirrors the wired ``_drain_next``: atomically FINALIZE + drain survivors
    (re-routing each as queued-new) and only THEN deregister.
    """
    survivors = await reg.finalize_and_drain(request_id)
    await reg.deregister(request_id)
    return survivors


async def _one_interleaving(seed: int, *, fixed: bool) -> bool:
    """Race a steer against completion teardown; return True iff NO steer lost.

    The turn's provider loop has just ENDED (status still RUNNING). The completion
    teardown and a fresh ``try_steer`` are launched concurrently and the scheduler
    interleaves them. A small seeded jitter shuffles which lands first.
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r1", session_id="s1", task=t, target=3, original_input="orig")

    accepted_running: list[str] = []
    converted_new: list[str] = []

    async def steer() -> None:
        # Seeded jitter so the steer sometimes lands before, sometimes during,
        # sometimes after the teardown's FINALIZING flip.
        for _ in range(random.randint(0, 3)):
            await asyncio.sleep(0)
        outcome = await reg.try_steer(
            "r1", "corr", session_id="s1", request_id_new="r2", target=3
        )
        (accepted_running if outcome == "STEER" else converted_new).append("corr")

    async def teardown() -> list[str]:
        for _ in range(random.randint(0, 3)):
            await asyncio.sleep(0)
        if fixed:
            return await _completion_teardown_fixed(reg, "r1")
        return await _completion_teardown_unfixed(reg, "r1")

    steer_task = asyncio.create_task(steer())
    teardown_task = asyncio.create_task(teardown())
    survivors, _ = await asyncio.wait_for(
        asyncio.gather(teardown_task, steer_task), 2.0
    )

    # Account for the single steer. It lands in EXACTLY one of:
    #   * converted_new  -> try_steer saw FINALIZING (or no live turn) -> queued-new
    #   * survivors      -> try_steer accepted onto RUNNING, then the teardown's
    #                       drain re-routed it as queued-new
    # A steer that is accepted_running but NOT in survivors was put onto a turn
    # that got deregistered without draining -> LOST.
    if "corr" in converted_new:
        return True
    # Accepted onto RUNNING and re-routed as a survivor -> safe; accepted but NOT
    # in survivors (deregistered without draining) -> the lost-steer hole.
    return bool(accepted_running) and "corr" in survivors


async def test_run_to_fail_steer_lost_without_finalize_drain() -> None:
    """RUN-TO-FAIL: the unfixed deregister-only seam LOSES a racing steer.

    With the old completion path (deregister, no finalize+drain), at least one
    interleaving accepts the steer onto the still-RUNNING turn and then
    deregisters it WITHOUT draining — the steer is silently dropped. This test
    asserts that loss is observable (it would FAIL if the seam were already
    correct), pinning the gap the fix closes.
    """
    lost_any = False
    for seed in range(200):
        random.seed(seed)
        no_loss = await _one_interleaving(seed, fixed=False)
        if not no_loss:
            lost_any = True
            break
    assert lost_any, (
        "expected the unfixed deregister-only completion seam to LOSE at least "
        "one racing steer across 200 interleavings — if this passes, the seam is "
        "already draining and the fix is unnecessary"
    )


async def test_no_lost_steers_across_completion_window_interleavings() -> None:
    """RUN-TO-PASS: the fixed seam (finalize_and_drain) loses ZERO steers.

    Across 200 seeded interleavings of (steer || completion-teardown), every
    steer is accounted for — either converted-to-NEW by ``try_steer`` seeing
    FINALIZING, or accepted-then-drained as a survivor. None is ever dropped onto
    a dead turn.
    """
    for seed in range(200):
        random.seed(seed)
        no_loss = await asyncio.wait_for(_one_interleaving(seed, fixed=True), 3.0)
        assert no_loss, f"seed={seed}: a steer was lost across the completion window"


async def test_finalize_and_drain_flips_finalizing_then_converts_concurrent_steer() -> None:
    """A steer that lands AFTER the FINALIZING flip is converted to queued-new.

    Drive the seam deterministically: finalize_and_drain flips RUNNING->FINALIZING
    under the lock, so a subsequent try_steer reads FINALIZING and returns "NEW"
    (enqueued), never onto the dead mailbox.
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-f", session_id="s1", task=t, target=5, original_input="orig"
    )
    survivors = await asyncio.wait_for(reg.finalize_and_drain("req-f"), 1.0)
    assert survivors == []
    assert turn.status is TurnStatus.FINALIZING
    # A steer arriving now sees FINALIZING -> converts to queued-new.
    outcome = await asyncio.wait_for(
        reg.try_steer("req-f", "late", session_id="s1", request_id_new="new-late", target=5),
        1.0,
    )
    assert outcome == "NEW"
    assert turn.steering_mailbox.empty()  # never put on the dead mailbox
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.request_id == "new-late" and nxt.target == 5
    await t


async def test_finalize_and_drain_reroutes_accepted_survivor() -> None:
    """A steer accepted onto RUNNING but not folded is drained as queued-new.

    Models the steer that landed in the window while status was still RUNNING:
    finalize_and_drain finds it in the mailbox and re-routes it as a queued-new
    turn (inheriting session/target), so it is never GC'd with the turn.
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-s", session_id="s1", task=t, target=8, original_input="orig"
    )
    # A steer accepted onto the RUNNING turn just before teardown.
    outcome = await reg.try_steer(
        "req-s", "accepted", session_id="s1", request_id_new="r-new", target=8
    )
    assert outcome == "STEER"
    survivors = await asyncio.wait_for(reg.finalize_and_drain("req-s"), 1.0)
    assert survivors == ["accepted"]
    assert turn.status is TurnStatus.FINALIZING
    assert turn.steering_mailbox.empty()
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.original_input == "accepted" and nxt.target == 8
    await t


async def test_finalize_and_drain_missing_turn_returns_empty() -> None:
    """Fail-safe: an already-deregistered/unknown turn yields [] (no crash)."""
    reg = TurnRegistry()
    survivors = await asyncio.wait_for(reg.finalize_and_drain("ghost"), 1.0)
    assert survivors == []


async def test_finalize_and_drain_idempotent_on_finalizing_turn() -> None:
    """Calling the seam on an already-FINALIZING turn drains + stays FINALIZING.

    The seam must not regress status nor crash if the turn already advanced (e.g.
    the loop finalized itself first). It drains any survivors and leaves status at
    FINALIZING (never back to RUNNING, never an illegal transition).
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-i", session_id="s1", task=t, target=None, original_input="orig"
    )
    assert await reg.cas_status("req-i", TurnStatus.RUNNING, TurnStatus.FINALIZING)
    turn.steering_mailbox.put_nowait("leftover")
    survivors = await asyncio.wait_for(reg.finalize_and_drain("req-i"), 1.0)
    assert survivors == ["leftover"]
    assert turn.status is TurnStatus.FINALIZING
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.original_input == "leftover"
    await t
