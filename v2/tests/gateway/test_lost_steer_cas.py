"""Task 11 (concurrent-msg §5.2) — lost-steer CAS guard.

§9 invariant 1 — the lost-steer guard: a steer must NEVER land in a dead turn's
mailbox. The invariant: a steer is either accepted by a still-RUNNING turn, or
converted to a queued-new turn — never enqueued onto a turn past its
finalization line.

The two halves, atomic under the per-turn lock:
  * Router/enqueue side — ``try_steer``: take the turn lock; read status; if
    RUNNING → ``steering_mailbox.put_nowait(text)`` + return "STEER"; if
    FINALIZING/DONE → ``enqueue(...)`` + return "NEW". Status-read + put are
    ATOMIC under the lock (no window where status reads RUNNING but the put
    lands after FINALIZING).
  * Completion-seam guard — ``finalize_and_drain``: the SOLE lost-steer guard,
    wired in the orchestrator's ``_drain_next`` BEFORE ``deregister``. Under the
    same per-turn lock it flips RUNNING→FINALIZING then drains+re-routes survivors,
    so a steer racing the turn's end is either converted-to-queued-new (a
    concurrent ``try_steer`` reads FINALIZING) or drained as a survivor.

NOTE (F051): the redundant finalize-side CAS primitives ``finalize_if_drained`` /
``drain_survivors`` were DEAD CODE (no production caller) and were REMOVED; the
window they targeted is closed by ``finalize_and_drain``. The randomized no-lost-
steer property test below now races the LIVE seam (``try_steer`` vs
``finalize_and_drain``). The deeper completion-window property is also covered by
tests/gateway/test_completion_finalize_drain.py.

Signature reconciliation: the plan's draft test used ``request_id=`` as the
new-turn id kwarg, which collides with the positional ``request_id`` (the turn
being steered). Reconciled to ``request_id_new=`` (the impl spec's name) for a
single coherent signature: ``try_steer(request_id, text, *, session_id,
request_id_new, target)``.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from stackowl.gateway.turn_registry import TurnRegistry, TurnStatus


async def _one_interleaving(seed: int) -> None:
    """Race a steer against the LIVE completion seam; assert ZERO lost steers.

    The live lost-steer guard is ``finalize_and_drain`` (flip RUNNING→FINALIZING +
    drain+re-route survivors), wired in the orchestrator's ``_drain_next`` before
    ``deregister``. This property test races a ``try_steer`` against it (the dead
    ``finalize_if_drained``/``drain_survivors`` primitives it used to exercise were
    removed in F051). Extracted to module scope so the inner coroutines bind
    ``reg``/``seed`` as real parameters (not closures over a loop var — ruff B023).
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r1", session_id="s1", task=t, target=None, original_input="orig")
    accepted: list[str] = []
    queued_new: list[str] = []

    async def steer() -> None:
        # Seeded jitter so the steer lands before / during / after the FINALIZING
        # flip across the 200 seeds.
        for _ in range(random.randint(0, 3)):
            await asyncio.sleep(0)
        outcome = await reg.try_steer(
            "r1", "corr", session_id="s1", request_id_new="r2", target=None
        )
        (accepted if outcome == "STEER" else queued_new).append("corr")

    async def finish() -> list[str]:
        for _ in range(random.randint(0, 3)):
            await asyncio.sleep(0)
        # The live completion seam: finalize+drain THEN deregister.
        survivors = await reg.finalize_and_drain("r1")
        await reg.deregister("r1")
        return survivors

    _, survivors = await asyncio.gather(steer(), finish())
    # exactly one of accepted/queued_new holds the steer; never neither
    assert len(accepted) + len(queued_new) == 1
    # ZERO lost steers: every steer is accounted for in EXACTLY one place —
    #   * NEW   → queued_new (try_steer saw FINALIZING / no live turn → converted)
    #   * STEER → accepted onto RUNNING, then finalize_and_drain re-routed it as a
    #             survivor. Accepted-but-NOT-a-survivor would be the lost-steer hole.
    routed = bool(queued_new) or (bool(accepted) and "corr" in survivors)
    assert routed, (
        f"seed={seed}: lost steer — accepted={accepted} queued_new={queued_new} "
        f"survivors={survivors}"
    )
    await t


@pytest.mark.asyncio
async def test_no_lost_steers_across_randomized_interleavings() -> None:
    for seed in range(200):
        random.seed(seed)
        await _one_interleaving(seed)


@pytest.mark.asyncio
async def test_try_steer_running_puts_and_returns_steer() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-run", session_id="s1", task=t, target=None, original_input="orig"
    )
    outcome = await reg.try_steer(
        "req-run", "more detail", session_id="s1", request_id_new="new-1", target=None
    )
    assert outcome == "STEER"
    # the text landed on the live mailbox
    assert turn.steering_mailbox.get_nowait() == "more detail"
    # no queued-new turn was created (it was steered, not converted)
    assert reg.pop_next("s1") is None
    await t


@pytest.mark.asyncio
async def test_try_steer_finalizing_returns_new_and_enqueues() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-fin", session_id="s1", task=t, target=None, original_input="orig")
    # advance to FINALIZING
    assert await reg.cas_status("req-fin", TurnStatus.RUNNING, TurnStatus.FINALIZING)
    outcome = await reg.try_steer(
        "req-fin", "too late", session_id="s1", request_id_new="new-2", target=7
    )
    assert outcome == "NEW"
    turn = reg.get("req-fin")
    assert turn is not None
    # NOT put on the (dead) turn's mailbox
    assert turn.steering_mailbox.empty()
    # converted to a queued-new turn instead
    nxt = reg.pop_next("s1")
    assert nxt is not None
    assert nxt.request_id == "new-2"
    assert nxt.original_input == "too late"
    assert nxt.target == 7
    await t


@pytest.mark.asyncio
async def test_try_steer_done_returns_new_and_enqueues() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-done", session_id="s1", task=t, target=None, original_input="orig")
    assert await reg.cas_status("req-done", TurnStatus.RUNNING, TurnStatus.FINALIZING)
    assert await reg.cas_status("req-done", TurnStatus.FINALIZING, TurnStatus.DONE)
    outcome = await reg.try_steer(
        "req-done", "way too late", session_id="s1", request_id_new="new-3", target=None
    )
    assert outcome == "NEW"
    nxt = reg.pop_next("s1")
    assert nxt is not None and nxt.request_id == "new-3"
    await t


@pytest.mark.asyncio
async def test_try_steer_missing_turn_returns_new() -> None:
    # Fail-safe: a steer for an unknown turn is converted to a queued-new turn
    # (never silently dropped, never an exception).
    reg = TurnRegistry()
    outcome = await reg.try_steer(
        "ghost", "hello", session_id="s9", request_id_new="new-g", target=None
    )
    assert outcome == "NEW"
    nxt = reg.pop_next("s9")
    assert nxt is not None and nxt.request_id == "new-g"


# NOTE (F051): the isolated tests that exercised ONLY the removed dead primitives
# (test_finalize_if_drained_* and test_drain_survivors_*) were deleted here. The
# shared helpers (_reroute_survivors_locked / _drain_mailbox_locked) and the live
# guard finalize_and_drain remain covered — by the property test above and by
# tests/gateway/test_completion_finalize_drain.py.
