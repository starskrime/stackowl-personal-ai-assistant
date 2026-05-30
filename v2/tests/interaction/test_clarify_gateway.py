"""Tests for :class:`ClarifyGateway` — the pending-clarify registry.

Covers (turn-yield): ask registers + delivers via a fake adapter;
cap-one-per-session replaces; try_resolve enforces session+channel and is
idempotent; mismatched channel/session refuses (entry stays); clear_session
drops + returns ids; sweep_expired drops old entries (injected clock);
no-adapter still registers but logs an undelivered delivery.

Covers (blocking-await): blocking ask sets an event; wait_for_answer returns the
answer after a concurrent try_resolve; wait_for_answer times out → (None, True) +
entry popped; try_resolve on a blocking entry sets answer + wakes the parked
waiter; cap-one replace abandons the prior parked waiter; clear_session /
sweep_expired wake a parked waiter (timed_out).
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.interaction.clarify_gateway import ClarifyGateway, PendingClarify


class _FakeAdapter:
    """Minimal stand-in capturing send_clarify calls (no real channel needed)."""

    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.calls: list[tuple[str, tuple[str, ...], str]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_clarify(
        self, question: str, choices: tuple[str, ...], clarify_id: str,
    ) -> None:
        self.calls.append((question, tuple(choices), clarify_id))


class _FakeClock:
    """Monotonic-shaped injectable clock for TTL/expiry tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------- ask


@pytest.mark.asyncio
async def test_ask_registers_and_delivers() -> None:
    adapter = _FakeAdapter("cli")
    gw = ClarifyGateway()
    gw.register_adapter("cli", adapter)  # type: ignore[arg-type]

    cid = await gw.ask("s1", "cli", "X or Y?", choices=("X", "Y"))

    assert isinstance(cid, str) and cid
    # Delivered exactly once with the same id.
    assert adapter.calls == [("X or Y?", ("X", "Y"), cid)]
    # Registered and resolvable.
    entry = gw.try_resolve("s1", "cli", "X")
    assert isinstance(entry, PendingClarify)
    assert entry.clarify_id == cid
    assert entry.question == "X or Y?"
    assert entry.choices == ("X", "Y")


@pytest.mark.asyncio
async def test_cap_one_per_session_replaces() -> None:
    adapter = _FakeAdapter("cli")
    gw = ClarifyGateway()
    gw.register_adapter("cli", adapter)  # type: ignore[arg-type]

    cid1 = await gw.ask("s1", "cli", "first?")
    cid2 = await gw.ask("s1", "cli", "second?")
    assert cid1 != cid2

    # Only the second survives — single pending per session.
    entry = gw.try_resolve("s1", "cli", "ans")
    assert entry is not None
    assert entry.clarify_id == cid2
    assert entry.question == "second?"
    # The first id is gone.
    assert gw.try_resolve("s1", "cli", "again") is None


@pytest.mark.asyncio
async def test_ask_no_adapter_still_registers() -> None:
    gw = ClarifyGateway()  # no adapter registered for "cli"
    cid = await gw.ask("s1", "cli", "open?")
    # Registered despite undelivered — resolution still works.
    entry = gw.try_resolve("s1", "cli", "ans")
    assert entry is not None
    assert entry.clarify_id == cid


# ----------------------------------------------------------- try_resolve


@pytest.mark.asyncio
async def test_try_resolve_is_idempotent() -> None:
    gw = ClarifyGateway()
    await gw.ask("s1", "cli", "q?")
    first = gw.try_resolve("s1", "cli", "a")
    assert first is not None
    # Second call after pop → None (pop is idempotent).
    assert gw.try_resolve("s1", "cli", "a") is None


@pytest.mark.asyncio
async def test_try_resolve_refuses_mismatched_channel() -> None:
    gw = ClarifyGateway()
    await gw.ask("s1", "cli", "q?")
    # Right session, wrong channel → None, entry stays.
    assert gw.try_resolve("s1", "telegram", "a") is None
    # Still resolvable on the correct channel.
    assert gw.try_resolve("s1", "cli", "a") is not None


@pytest.mark.asyncio
async def test_try_resolve_refuses_mismatched_session() -> None:
    gw = ClarifyGateway()
    await gw.ask("s1", "cli", "q?")
    # Wrong session, right channel → None, entry stays.
    assert gw.try_resolve("s2", "cli", "a") is None
    assert gw.try_resolve("s1", "cli", "a") is not None


@pytest.mark.asyncio
async def test_try_resolve_no_pending_returns_none() -> None:
    gw = ClarifyGateway()
    assert gw.try_resolve("s1", "cli", "a") is None


# ----------------------------------------------------------- clear_session


@pytest.mark.asyncio
async def test_clear_session_drops_and_returns_ids() -> None:
    gw = ClarifyGateway()
    cid_a = await gw.ask("s1", "cli", "qa?")
    await gw.ask("s2", "cli", "qb?")  # different session — must survive

    dropped = gw.clear_session("s1")
    assert dropped == [cid_a]
    # s1 gone, s2 intact.
    assert gw.try_resolve("s1", "cli", "a") is None
    assert gw.try_resolve("s2", "cli", "a") is not None


@pytest.mark.asyncio
async def test_clear_session_no_entries_returns_empty() -> None:
    gw = ClarifyGateway()
    assert gw.clear_session("nope") == []


# ----------------------------------------------------------- sweep_expired


@pytest.mark.asyncio
async def test_sweep_expired_drops_old_entries() -> None:
    clock = _FakeClock()
    gw = ClarifyGateway(time_fn=clock)

    clock.now = 100.0
    await gw.ask("s1", "cli", "old?")
    clock.now = 105.0
    await gw.ask("s2", "cli", "new?")

    # TTL=10s; at now=112 the first (created at 100, age 12) expires, the
    # second (created at 105, age 7) survives.
    clock.now = 112.0
    n = gw.sweep_expired(10.0)
    assert n == 1
    assert gw.try_resolve("s1", "cli", "a") is None
    assert gw.try_resolve("s2", "cli", "a") is not None


@pytest.mark.asyncio
async def test_sweep_expired_nothing_to_drop() -> None:
    clock = _FakeClock()
    gw = ClarifyGateway(time_fn=clock)
    clock.now = 100.0
    await gw.ask("s1", "cli", "q?")
    clock.now = 105.0
    assert gw.sweep_expired(60.0) == 0
    assert gw.try_resolve("s1", "cli", "a") is not None


# ------------------------------------------------------------- blocking ask


@pytest.mark.asyncio
async def test_blocking_ask_sets_event() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)
    # The entry carries an unset asyncio.Event (a waiter can park on it).
    entry = gw._pending[cid]
    assert isinstance(entry.event, asyncio.Event)
    assert not entry.event.is_set()
    assert entry.answer is None


@pytest.mark.asyncio
async def test_non_blocking_ask_has_no_event() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?")  # default blocking=False
    assert gw._pending[cid].event is None


@pytest.mark.asyncio
async def test_wait_for_answer_resolved_concurrently() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

    # Park the waiter, then deliver the answer via try_resolve.
    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)  # let the waiter actually park on the event
    match = gw.try_resolve("s1", "cli", "blue")
    answer, timed_out = await waiter

    assert answer == "blue"
    assert timed_out is False
    # try_resolve returned the (popped) entry with its event now set — the
    # router's signal that this was a blocking (in-turn) resolve.
    assert match is not None
    assert match.event is not None and match.event.is_set()
    assert match.answer == "blue"


@pytest.mark.asyncio
async def test_wait_for_answer_resolve_before_park() -> None:
    """Regression: a reply that lands BEFORE the tool parks must not be lost.

    ``ask`` internally awaits ``send_clarify`` — a real yield point — so the
    decoupled gateway loop can run a reply and call ``try_resolve`` BEFORE the
    tool ever reaches ``wait_for_answer``. Pre-fix ``try_resolve`` popped the
    entry, so the later ``wait_for_answer`` saw ``None`` and reported a spurious
    timeout, silently discarding the user's answer. With waiter-owned pop the
    answer survives.
    """
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "fav colour?", blocking=True)

    # Resolve FIRST — no waiter parked yet.
    match = gw.try_resolve("s1", "cli", "blue")
    assert match is not None
    assert match.event is not None and match.event.is_set()
    # Entry still present — try_resolve must NOT pop a blocking entry.
    assert cid in gw._pending

    # Now park: event already set → returns the answer without waiting.
    answer, timed_out = await gw.wait_for_answer(cid, timeout=5.0)
    assert answer == "blue"
    assert timed_out is False
    # The waiter owns the pop — entry is gone afterwards.
    assert cid not in gw._pending


@pytest.mark.asyncio
async def test_wait_for_answer_resolve_real_empty_string() -> None:
    """An empty-string answer ("") is a REAL answer, not a timeout.

    The ``is None`` disambiguation (real answer vs abandoned waiter) must
    survive the new waiter-owned pop ownership.
    """
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "anything?", blocking=True)

    match = gw.try_resolve("s1", "cli", "")
    assert match is not None

    answer, timed_out = await gw.wait_for_answer(cid, timeout=5.0)
    assert answer == ""
    assert timed_out is False
    assert cid not in gw._pending


@pytest.mark.asyncio
async def test_double_resolve_before_park_delivers_once() -> None:
    """Two replies in the resolve-before-park window are benign.

    The second re-matches the same (not-yet-popped) blocking entry and re-sets
    it. The waiter still wakes once and reads the current answer; the entry is
    gone afterwards; a third try_resolve finds nothing.
    """
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)

    first = gw.try_resolve("s1", "cli", "blue")
    second = gw.try_resolve("s1", "cli", "green")  # re-matches same entry (benign)
    assert first is not None
    assert second is first  # same entry re-set, never a different one

    answer, timed_out = await gw.wait_for_answer(cid, timeout=5.0)
    assert answer == "green"  # last-writer-wins; an answer is delivered
    assert timed_out is False
    assert cid not in gw._pending

    # Entry gone → a further resolve finds nothing.
    assert gw.try_resolve("s1", "cli", "red") is None


@pytest.mark.asyncio
async def test_abandon_while_parked_returns_timed_out() -> None:
    """A parked waiter whose entry is cleared wakes with (None, True).

    Because ``wait_for_answer`` holds the entry reference, an abandonment that
    sets the event with ``answer=None`` is still read correctly as timed_out.
    """
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)

    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)  # park on the entry's event

    dropped = gw.clear_session("s1")
    answer, timed_out = await waiter

    assert dropped == [cid]
    assert answer is None
    assert timed_out is True


@pytest.mark.asyncio
async def test_wait_for_answer_times_out_and_pops() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)

    answer, timed_out = await gw.wait_for_answer(cid, timeout=0.05)

    assert answer is None
    assert timed_out is True
    # Entry popped so a late reply is ignored.
    assert cid not in gw._pending
    assert gw.try_resolve("s1", "cli", "late") is None


@pytest.mark.asyncio
async def test_wait_for_answer_absent_entry_returns_timed_out() -> None:
    gw = ClarifyGateway()
    answer, timed_out = await gw.wait_for_answer("nope", timeout=0.05)
    assert answer is None
    assert timed_out is True


@pytest.mark.asyncio
async def test_try_resolve_signals_blocking_waiter() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)
    entry = gw._pending[cid]

    match = gw.try_resolve("s1", "cli", "answer-text")

    # Answer written and event set — a parked waiter would wake.
    assert match is entry
    assert entry.answer == "answer-text"
    assert entry.event is not None and entry.event.is_set()


@pytest.mark.asyncio
async def test_cap_one_replace_abandons_prior_blocking_waiter() -> None:
    gw = ClarifyGateway()
    cid1 = await gw.ask("s1", "cli", "first?", blocking=True)

    waiter = asyncio.ensure_future(gw.wait_for_answer(cid1, timeout=5.0))
    await asyncio.sleep(0)  # park on the first entry's event

    # A second ask for the same session replaces the first and must wake (abandon)
    # the orphaned waiter rather than leak it.
    cid2 = await gw.ask("s1", "cli", "second?", blocking=True)
    answer, timed_out = await waiter

    assert cid1 != cid2
    assert answer is None
    assert timed_out is True  # abandoned → woken with no answer
    # Only the second entry survives.
    assert cid1 not in gw._pending
    assert cid2 in gw._pending


@pytest.mark.asyncio
async def test_wait_for_answer_cancelled_pops_entry_and_reraises() -> None:
    """A cancelled parked waiter must POP its entry and RE-RAISE CancelledError.

    Regression (party-mode B-2): on ``asyncio.CancelledError`` the pre-fix code
    re-raised WITHOUT popping, leaking a ghost in ``_pending``. A later
    ``try_resolve`` for that session then matched the ghost, set a dead event,
    and silently dropped the real answer. The fix pops the entry before
    re-raising (cooperative cancellation still propagates).
    """
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)

    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)  # let the waiter actually park on the event

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # The ghost is gone — no leak. A subsequent try_resolve finds nothing to
    # mis-match against.
    assert cid not in gw._pending
    assert gw.try_resolve("s1", "cli", "real-answer") is None


@pytest.mark.asyncio
async def test_clear_session_wakes_blocking_waiter() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask("s1", "cli", "q?", blocking=True)

    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)

    dropped = gw.clear_session("s1")
    answer, timed_out = await waiter

    assert dropped == [cid]
    assert answer is None
    assert timed_out is True


@pytest.mark.asyncio
async def test_sweep_expired_wakes_blocking_waiter() -> None:
    clock = _FakeClock()
    gw = ClarifyGateway(time_fn=clock)
    clock.now = 100.0
    cid = await gw.ask("s1", "cli", "q?", blocking=True)

    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)

    clock.now = 200.0
    n = gw.sweep_expired(10.0)
    answer, timed_out = await waiter

    assert n == 1
    assert answer is None
    assert timed_out is True
