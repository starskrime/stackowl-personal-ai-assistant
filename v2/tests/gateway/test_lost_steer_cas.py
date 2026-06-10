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
  * Loop/finalize side — ``finalize_if_drained``: take the turn lock; if the
    mailbox is non-empty → return False (caller loops again, does NOT finalize
    with pending steers); else CAS RUNNING→FINALIZING and return True.
  * Teardown — ``drain_survivors``: drain remaining mailbox items and re-route
    each as a queued-new turn (``enqueue``), returning them. A discarded steer
    is a lost instruction → convert, never GC.

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
    """Run a single steer-vs-finalize interleaving; assert ZERO lost steers.

    Extracted to module scope so the inner coroutines bind ``reg``/``seed`` as
    real parameters (not closures over a loop variable — ruff B023). The body is
    the plan's draft, with ``finish()`` modeling the execute loop's FOLD between
    ``finalize_if_drained`` checks (the live path drains the mailbox via
    ``make_steering_callback`` at each iteration boundary) so an accepted steer is
    consumed rather than spinning forever.
    """
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("r1", session_id="s1", task=t, target=None, original_input="orig")
    accepted: list[str] = []
    queued_new: list[str] = []
    folded: list[str] = []

    async def steer() -> None:
        outcome = await reg.try_steer(
            "r1", "corr", session_id="s1", request_id_new="r2", target=None
        )
        (accepted if outcome == "STEER" else queued_new).append("corr")

    async def finish() -> None:
        # finalize_if_drained returns False while a steer is pending; the loop then
        # FOLDS (drains) it — exactly what make_steering_callback does at each
        # iteration boundary — before re-checking. A bounded guard prevents any
        # accidental infinite loop from masquerading as a hang.
        guard = 0
        while not await reg.finalize_if_drained("r1"):
            turn = reg.get("r1")
            assert turn is not None
            while not turn.steering_mailbox.empty():
                folded.append(turn.steering_mailbox.get_nowait())
            guard += 1
            assert guard < 100, f"seed={seed}: finalize loop did not converge"
            await asyncio.sleep(0)
        await reg.cas_status("r1", TurnStatus.FINALIZING, TurnStatus.DONE)

    await asyncio.gather(steer(), finish())
    # exactly one of accepted/queued_new holds the steer; never neither
    assert len(accepted) + len(queued_new) == 1
    # teardown re-route: any steer accepted-but-not-folded becomes queued-new
    survivors = await reg.drain_survivors("r1")
    # ZERO lost steers: every steer is accounted for in EXACTLY one place —
    #   * NEW    → queued_new (converted because the turn was past finalization)
    #   * STEER + folded by the running loop before it finalized → folded
    #   * STEER + arrived after the last fold/finalize → survivors (re-routed)
    # In NO interleaving is a steer silently dropped onto a finalized turn.
    total_routed = len(queued_new) + len(folded) + len(survivors)
    assert total_routed == 1, (
        f"seed={seed}: lost steer — accepted={accepted} queued_new={queued_new} "
        f"folded={folded} survivors={survivors}"
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


@pytest.mark.asyncio
async def test_finalize_if_drained_false_when_pending() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-p", session_id="s1", task=t, target=None, original_input="orig"
    )
    turn.steering_mailbox.put_nowait("pending steer")
    assert await reg.finalize_if_drained("req-p") is False
    # did NOT advance — still RUNNING with the pending steer intact
    assert turn.status is TurnStatus.RUNNING
    assert turn.steering_mailbox.get_nowait() == "pending steer"
    await t


@pytest.mark.asyncio
async def test_finalize_if_drained_true_when_empty_and_cases() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-e", session_id="s1", task=t, target=None, original_input="orig"
    )
    assert turn.steering_mailbox.empty()
    assert await reg.finalize_if_drained("req-e") is True
    # CAS RUNNING -> FINALIZING happened
    assert turn.status is TurnStatus.FINALIZING
    await t


@pytest.mark.asyncio
async def test_finalize_if_drained_missing_turn_returns_true() -> None:
    # A deregistered/unknown turn is already past its finalization line → True
    # (caller stops looping; there is nothing to finalize).
    reg = TurnRegistry()
    assert await reg.finalize_if_drained("nope") is True


@pytest.mark.asyncio
async def test_drain_survivors_reroutes_each_as_queued_new() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register(
        "req-s", session_id="s1", task=t, target=4, original_input="orig"
    )
    turn.steering_mailbox.put_nowait("survivor one")
    turn.steering_mailbox.put_nowait("survivor two")
    survivors = await reg.drain_survivors("req-s")
    assert survivors == ["survivor one", "survivor two"]
    # mailbox fully drained
    assert turn.steering_mailbox.empty()
    # each re-routed as a queued-new turn on the SAME session, FIFO order,
    # inheriting the turn's target
    n1 = reg.pop_next("s1")
    n2 = reg.pop_next("s1")
    assert n1 is not None and n1.original_input == "survivor one" and n1.target == 4
    assert n2 is not None and n2.original_input == "survivor two" and n2.target == 4
    assert reg.pop_next("s1") is None
    await t


@pytest.mark.asyncio
async def test_drain_survivors_empty_mailbox_returns_empty() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-ne", session_id="s1", task=t, target=None, original_input="orig")
    assert await reg.drain_survivors("req-ne") == []
    await t


@pytest.mark.asyncio
async def test_drain_survivors_missing_turn_returns_empty() -> None:
    reg = TurnRegistry()
    assert await reg.drain_survivors("absent") == []
