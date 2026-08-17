"""Slice 3 — a chat turn is a task on the ONE loop.

BAKIR, 2026-08-17: *"if I am pinging in the Telegram chat about some question,
that's also task."* And: *"loop should go understand, find the answer, return back
answer to the Telegram. And if it's delivered to me, it means loop is completed."*

WHAT THIS SLICE DOES, STATED PLAINLY so the commit cannot overclaim. Every chat
turn now gets a durable row at ingress, and that row completes ONLY when the reply
actually reached the user. The existing pipeline still PRODUCES the reply on the
fast path — the latency of a working turn is unchanged — and the loop becomes the
durable owner of every turn: if the reply never lands, because the process died or
the provider was out, the row's lease expires and the loop re-drives it with what
already failed.

WHY NOT FLIP THE WHOLE PATH IN ONE STEP. `_dispatch_turn` is the hottest code in
the platform: stream registry, clarify pump, turn registry, parliament and command
routing, degraded-provider handling. Rewriting it so the reply is produced inside a
worker would put every reply at risk in a single change. The row and the completion
rule are what make the loop authoritative; moving production into the worker is a
later, provable step on top of a seam that already exists.

REUSE, NOT A SECOND ENGINE. A recovered chat task is re-driven by the EXISTING
RetryActuator, which already re-runs a floored turn's goal and delivers it. CLAUDE.md
forbids a second path that runs work, and this is exactly that case.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.turn_task import complete_turn_task, enqueue_turn_task

pytestmark = pytest.mark.asyncio


class _Store:
    """Double with the REAL store surface the bridge calls."""

    def __init__(self, *, boom: bool = False) -> None:
        self.enqueued: list[object] = []
        self.delivered: list[tuple[str, str]] = []
        self._boom = boom

    async def enqueue(self, task: object) -> None:
        if self._boom:
            raise RuntimeError("db gone")
        self.enqueued.append(task)

    async def mark_delivered(self, task_id: str, *, result: str) -> None:
        if self._boom:
            raise RuntimeError("db gone")
        self.delivered.append((task_id, result))


class TestEveryChatTurnBecomesARow:
    async def test_a_turn_is_enqueued_with_its_destination(self) -> None:
        """The destination is what makes completion checkable. Without it "done"
        would be the function returning, which is the overclaim this platform keeps
        finding."""
        store = _Store()

        await enqueue_turn_task(
            store, trace_id="tr-1", goal="what is your name?",
            channel="telegram", chat_id="72055773", session_key="lane",
            owl_name="secretary",
        )

        assert len(store.enqueued) == 1
        t = store.enqueued[0]
        assert t.task_id == "tr-1"  # type: ignore[attr-defined]
        assert t.destination == "telegram:72055773"  # type: ignore[attr-defined]
        assert t.trigger_kind == "chat"  # type: ignore[attr-defined]
        assert t.status == "running"  # type: ignore[attr-defined]

    async def test_it_is_born_RUNNING_not_pending(self) -> None:
        """The fast path is already producing this reply. A `pending` row would be
        claimed by the loop and run a SECOND time — the same turn answered twice.
        It becomes claimable only when its lease expires, i.e. when the fast path
        demonstrably did not finish.
        """
        store = _Store()

        await enqueue_turn_task(store, trace_id="tr-1", goal="hi", channel="cli")

        t = store.enqueued[0]
        assert t.status == "running"  # type: ignore[attr-defined]
        assert t.lease_owner is not None  # type: ignore[attr-defined]

    async def test_a_channel_with_no_address_still_gets_a_destination(self) -> None:
        """CLI addresses its single terminal implicitly — there is no chat id, but
        there is still somewhere the answer must land."""
        store = _Store()

        await enqueue_turn_task(store, trace_id="tr-1", goal="hi", channel="cli")

        assert store.enqueued[0].destination == "cli"  # type: ignore[attr-defined]


class TestCompletionIsDelivery:
    async def test_a_delivered_reply_completes_the_task(self) -> None:
        store = _Store()

        await complete_turn_task(store, trace_id="tr-1", result="Your name is Friday.")

        assert store.delivered == [("tr-1", "Your name is Friday.")]

    async def test_an_EMPTY_reply_does_not_complete_it(self) -> None:
        """Nothing reached the user, so nothing was achieved. Leaving the row
        un-completed is what lets the loop recover the turn instead of recording a
        success that never happened."""
        store = _Store()

        await complete_turn_task(store, trace_id="tr-1", result="   ")

        assert store.delivered == []


class TestItNeverCostsTheTurn:
    async def test_an_enqueue_failure_does_not_break_the_reply(self) -> None:
        """The durable row is a SAFETY NET. A net that can drop the thing it is
        protecting is worse than no net: the user's reply must not depend on the
        task table being writable."""
        await enqueue_turn_task(
            _Store(boom=True), trace_id="tr-1", goal="hi", channel="cli"
        )  # must not raise

    async def test_a_completion_failure_does_not_break_the_reply(self) -> None:
        await complete_turn_task(
            _Store(boom=True), trace_id="tr-1", result="answered"
        )  # must not raise

    async def test_no_store_wired_is_a_no_op(self) -> None:
        """Every existing construction site and test that has no task store keeps
        working byte-identically."""
        await enqueue_turn_task(None, trace_id="tr-1", goal="hi", channel="cli")
        await complete_turn_task(None, trace_id="tr-1", result="answered")


class TestTheRealChannelTypes:
    """The types the LIVE adapters actually pass.

    The first live run of this bridge failed on EVERY turn with "'int' object has
    no attribute 'strip'": Telegram chat ids are ints, and the tests above passed
    the string "72055773". A double that had stopped resembling the real thing —
    which is exactly the defect shape this repo warns about, and it survived a
    green suite until real traffic hit it.
    """

    async def test_an_INT_chat_id_is_accepted(self) -> None:
        store = _Store()

        await enqueue_turn_task(
            store, trace_id="tr-1", goal="hi", channel="telegram", chat_id=72055773,
        )

        assert store.enqueued, "an int chat id broke the enqueue"
        assert store.enqueued[0].destination == "telegram:72055773"  # type: ignore[attr-defined]

    async def test_a_None_chat_id_is_accepted(self) -> None:
        store = _Store()

        await enqueue_turn_task(store, trace_id="tr-1", goal="hi",
                                channel="telegram", chat_id=None)

        assert store.enqueued[0].destination == "telegram"  # type: ignore[attr-defined]

    async def test_a_non_string_channel_does_not_break_it(self) -> None:
        """Nothing passes this today; it is asserted so the next type surprise
        degrades to a usable destination instead of losing the whole row."""
        store = _Store()

        await enqueue_turn_task(store, trace_id="tr-1", goal="hi",
                                channel=None, chat_id=123)

        assert store.enqueued[0].destination == "cli:123"  # type: ignore[attr-defined]
