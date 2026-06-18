from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.turn_registry import Turn, TurnRegistry, TurnStatus


@pytest.mark.asyncio
async def test_status_cas_is_one_way() -> None:
    reg = TurnRegistry()
    task = asyncio.create_task(asyncio.sleep(0))
    turn = await reg.register("req-1", session_id="s1", task=task, target=None, original_input="hi")
    assert turn.status is TurnStatus.RUNNING
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING) is True
    # backward / skip transitions rejected
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.DONE) is False
    assert await reg.cas_status("req-1", TurnStatus.FINALIZING, TurnStatus.DONE) is True
    await task


@pytest.mark.asyncio
async def test_one_running_per_session_plus_fifo_queue() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    assert reg.running("s1") is not None
    reg.enqueue("s1", original_input="b", request_id="req-2", target=None)
    reg.enqueue("s1", original_input="c", request_id="req-3", target=None)
    first = reg.pop_next("s1")
    assert first is not None and first.original_input == "b"
    second = reg.pop_next("s1")
    assert second is not None and second.original_input == "c"  # FIFO
    assert reg.pop_next("s1") is None
    await t


@pytest.mark.asyncio
async def test_deregister_clears_running() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    await reg.deregister("req-1")
    assert reg.running("s1") is None
    assert reg.get("req-1") is None
    await t


@pytest.mark.asyncio
async def test_sweeper_snapshots_then_acts_and_reaps_done_without_status() -> None:
    reg = TurnRegistry()

    async def quick() -> None:
        return None

    t = asyncio.create_task(quick())
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    await t  # task done, status still RUNNING (lost the finally race)
    reaped = await reg.sweep(ttl_seconds=0.0)
    assert "req-1" in reaped
    assert reg.get("req-1") is None


@pytest.mark.asyncio
async def test_cas_full_rejection_matrix() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")

    # RUNNING -> FINALIZING : legal one-way step
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.DONE) is False  # skip rejected
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING) is True
    # status is now FINALIZING
    # FINALIZING -> RUNNING : backward rejected
    assert await reg.cas_status("req-1", TurnStatus.FINALIZING, TurnStatus.RUNNING) is False
    # expect-mismatch : status is FINALIZING, expect RUNNING -> rejected
    assert await reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING) is False
    # FINALIZING -> DONE : legal one-way step
    assert await reg.cas_status("req-1", TurnStatus.FINALIZING, TurnStatus.DONE) is True
    # unknown request_id : rejected
    assert await reg.cas_status("nope", TurnStatus.RUNNING, TurnStatus.FINALIZING) is False
    await t


@pytest.mark.asyncio
async def test_concurrent_cas_exactly_one_winner() -> None:
    # Run several seeds/iterations: the per-turn lock must serialize two
    # simultaneous RUNNING->FINALIZING transitions so EXACTLY ONE wins.
    for _ in range(20):
        reg = TurnRegistry()
        t = asyncio.create_task(asyncio.sleep(0))
        await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")

        results = await asyncio.gather(
            reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING),
            reg.cas_status("req-1", TurnStatus.RUNNING, TurnStatus.FINALIZING),
        )
        assert results.count(True) == 1, results
        assert results.count(False) == 1, results
        assert reg.get("req-1").status is TurnStatus.FINALIZING  # type: ignore[union-attr]
        await t


@pytest.mark.asyncio
async def test_sweeper_selectivity_spares_live_within_ttl() -> None:
    reg = TurnRegistry()

    # dead turn: task already completed, status still RUNNING -> must be reaped
    dead = asyncio.create_task(asyncio.sleep(0))
    await dead
    await reg.register("dead", session_id="s-dead", task=dead, target=None, original_input="a")

    # live turn: task NOT done, within TTL -> must be spared
    live = asyncio.create_task(asyncio.sleep(60))
    await reg.register("live", session_id="s-live", task=live, target=None, original_input="b")

    reaped = await reg.sweep(ttl_seconds=120.0)

    assert reaped == ["dead"]
    assert reg.get("dead") is None
    assert reg.get("live") is not None  # spared
    assert reg.running("s-live") is not None

    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live


@pytest.mark.asyncio
async def test_sweep_does_not_reap_live_running_turn() -> None:
    # F050 — the bare-expired foot-gun: a still-RUNNING turn (task NOT done) far
    # past TTL must be SPARED. Reaping it frees _running and lets a concurrent
    # same-session message start a SECOND running turn → two writers to one chat
    # history (the race project_concurrent_message_handling deleted). Fails against
    # the OR'd ``expired``-alone leg.
    reg = TurnRegistry()
    live = asyncio.create_task(asyncio.sleep(60))
    await reg.register("live", session_id="s1", task=live, target=None, original_input="a")

    reaped = await reg.sweep(ttl_seconds=0.0)  # started_at already past → expired True

    assert reaped == []
    assert reg.get("live") is not None  # NOT reaped
    running = reg.running("s1")
    assert running is not None and running.turn_id == "live"  # _running slot intact

    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live


@pytest.mark.asyncio
async def test_sweep_reaps_done_not_DONE_turn() -> None:
    # A turn whose task is done() but status != DONE (lost the finally race) is the
    # legitimate wedge — reap it and free _running.
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("stuck", session_id="s1", task=t, target=None, original_input="a")
    await t  # done, status still RUNNING

    reaped = await reg.sweep(ttl_seconds=999_999.0)  # not expired — done-leg must fire

    assert reaped == ["stuck"]
    assert reg.get("stuck") is None
    assert reg.running("s1") is None


@pytest.mark.asyncio
async def test_sweep_fires_stranded_drain_after_reap() -> None:
    # F050 stranded-session: a reap that frees a _running slot for a session with a
    # queued intake must surface that session to the drain seam (no fake success —
    # reaped AND surfaced). Spy on the registered callback.
    calls: list[int] = []

    async def _drainer() -> None:
        calls.append(1)

    reg = TurnRegistry()
    reg.set_stranded_drainer(_drainer)
    t = asyncio.create_task(asyncio.sleep(0))
    await reg.register("stuck", session_id="s1", task=t, target=None, original_input="a")
    await t

    await reg.sweep(ttl_seconds=0.0)

    assert calls == [1]  # drainer fired exactly once after the reap


@pytest.mark.asyncio
async def test_sweep_no_reap_does_not_fire_drainer() -> None:
    calls: list[int] = []

    async def _drainer() -> None:
        calls.append(1)

    reg = TurnRegistry()
    reg.set_stranded_drainer(_drainer)
    live = asyncio.create_task(asyncio.sleep(60))
    await reg.register("live", session_id="s1", task=live, target=None, original_input="a")

    await reg.sweep(ttl_seconds=0.0)  # nothing reaped

    assert calls == []  # no spurious drain
    live.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live


@pytest.mark.asyncio
async def test_sweeper_reaps_all_multi_entry_without_raising() -> None:
    # >1 reapable entry is required to expose iterate-and-mutate ("dict changed
    # size") risk; the snapshot-then-act loop must reap all without raising.
    reg = TurnRegistry()
    rids = ["m1", "m2", "m3", "m4"]
    tasks = []
    for i, rid in enumerate(rids):
        t = asyncio.create_task(asyncio.sleep(0))
        await t  # done -> reapable
        await reg.register(rid, session_id=f"s{i}", task=t, target=None, original_input="x")
        tasks.append(t)

    reaped = await reg.sweep(ttl_seconds=0.0)

    assert sorted(reaped) == sorted(rids)
    for rid in rids:
        assert reg.get(rid) is None


@pytest.mark.asyncio
async def test_steering_mailbox_is_bounded() -> None:
    reg = TurnRegistry()
    t = asyncio.create_task(asyncio.sleep(0))
    turn: Turn = await reg.register("req-1", session_id="s1", task=t, target=None, original_input="a")
    assert isinstance(turn.steering_mailbox, asyncio.Queue)
    assert turn.steering_mailbox.maxsize == 8
    assert turn.stop_requested is False
    await t
