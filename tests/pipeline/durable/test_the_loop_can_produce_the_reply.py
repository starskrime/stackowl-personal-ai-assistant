"""The last piece — the loop PRODUCES the answer, not only recovers it.

BAKIR, 2026-08-17: *"loop should go understand, find the answer, return back answer
to the Telegram. And if it's delivered to me, it means loop is completed."*

Until now the loop owned RECOVERY: the fast path produced every reply and the loop
picked up what it dropped. This makes the loop the primary path, behind a flag.

WHY A FLAG, AND WHY IT DEFAULTS OFF. This is not caution about the code — it is a
real trade the operator has to make. A loop-produced reply is delivered by the
worker in ONE PIECE when the work is finished, so the streaming/progress path that
makes a slow turn feel alive is bypassed. On this hardware a multi-tool turn runs
minutes, and minutes of silence reads as broken. The durable row, the delivery rule
and the retry ladder are identical either way; only who writes the answer changes.

THE PROPERTY THAT MUST HOLD IN BOTH MODES: exactly ONE producer. A turn that is both
run by the fast path and claimed by the loop is answered twice, and on Telegram the
user sees two replies to one question. That is the failure this file exists to
prevent, and it is why the row's initial STATUS is the whole mechanism — `running`
with a lease means "the fast path has it", `pending` means "the loop may take it".
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.turn_task import enqueue_turn_task, loop_produces_replies

pytestmark = pytest.mark.asyncio


class _Store:
    def __init__(self) -> None:
        self.enqueued: list[object] = []

    async def enqueue(self, task: object) -> None:
        self.enqueued.append(task)


class _Loop:
    def __init__(self) -> None:
        self.woken = 0

    def wake(self) -> None:
        self.woken += 1


class TestExactlyOneProducer:
    async def test_fast_path_mode_claims_the_row_so_the_loop_cannot(self) -> None:
        """Default. The row is born `running` WITH a lease: the fast path holds it,
        and the loop only ever sees it if that lease expires — i.e. if the fast
        path demonstrably did not finish."""
        store = _Store()

        await enqueue_turn_task(store, trace_id="t", goal="hi", channel="cli",
                                loop_produces=False)

        row = store.enqueued[0]
        assert row.status == "running"  # type: ignore[attr-defined]
        assert row.lease_owner is not None  # type: ignore[attr-defined]

    async def test_loop_mode_leaves_the_row_CLAIMABLE(self) -> None:
        """The inverse. Nothing else is going to answer this, so the row must be
        `pending` and unleased or the loop will never pick it up and the user gets
        silence — the worst outcome of the two."""
        store = _Store()

        await enqueue_turn_task(store, trace_id="t", goal="hi", channel="cli",
                                loop_produces=True)

        row = store.enqueued[0]
        assert row.status == "pending"  # type: ignore[attr-defined]
        assert row.lease_owner is None  # type: ignore[attr-defined]

    async def test_the_destination_survives_the_mode(self) -> None:
        """Whoever produces it, completion still means DELIVERED — so the row needs
        its destination in both modes."""
        store = _Store()

        for mode in (True, False):
            await enqueue_turn_task(store, trace_id="t", goal="hi",
                                    channel="telegram", chat_id=72055773,
                                    loop_produces=mode)

        assert all(r.destination == "telegram:72055773" for r in store.enqueued)  # type: ignore[attr-defined]


class TestTheUserDoesNotWaitForATick:
    async def test_loop_mode_WAKES_the_loop_immediately(self) -> None:
        """A five-second tick is fine for a sweep and awful for someone waiting on
        a reply. The enqueue wakes the loop so the answer starts now; the tick stays
        the safety net."""
        loop = _Loop()

        await enqueue_turn_task(_Store(), trace_id="t", goal="hi", channel="cli",
                                loop_produces=True, loop=loop)

        assert loop.woken == 1

    async def test_fast_path_mode_does_NOT_wake_the_loop(self) -> None:
        """Nothing to claim — the fast path holds the row. Waking would only spend
        a pass discovering there is no work."""
        loop = _Loop()

        await enqueue_turn_task(_Store(), trace_id="t", goal="hi", channel="cli",
                                loop_produces=False, loop=loop)

        assert loop.woken == 0

    async def test_a_missing_loop_does_not_break_the_enqueue(self) -> None:
        """The row still has to be written even if the loop is not wired — it is
        what makes the turn recoverable at all."""
        store = _Store()

        await enqueue_turn_task(store, trace_id="t", goal="hi", channel="cli",
                                loop_produces=True, loop=None)

        assert store.enqueued

    async def test_a_wake_that_raises_does_not_break_the_enqueue(self) -> None:
        class _Boom:
            def wake(self) -> None:
                raise RuntimeError("loop is gone")

        store = _Store()

        await enqueue_turn_task(store, trace_id="t", goal="hi", channel="cli",
                                loop_produces=True, loop=_Boom())

        assert store.enqueued, "a failed wake cost the durable row"


class TestTheModeIsReadFromConfig:
    async def test_it_is_off_unless_configured_on(self) -> None:
        """The safe default has to be the one you get when nothing is wired: with
        no services and no settings, the fast path keeps producing."""
        assert loop_produces_replies(None) is False

    async def test_a_configured_true_turns_it_on(self) -> None:
        from types import SimpleNamespace

        from stackowl.config.settings import Settings

        cfg = Settings()
        on = cfg.model_copy(update={
            "task_loop": cfg.task_loop.model_copy(update={"produce_replies": True})
        })

        assert loop_produces_replies(SimpleNamespace(settings=on)) is True

    async def test_unreadable_settings_fall_back_to_the_fast_path(self) -> None:
        """Degrade toward the mode that definitely answers the user. A config
        error must not leave a turn with nobody producing it."""
        from types import SimpleNamespace

        class _Boom:
            @property
            def task_loop(self):  # noqa: ANN202
                raise RuntimeError("config unreadable")

        assert loop_produces_replies(SimpleNamespace(settings=_Boom())) is False


class TestEveryGatewayCanReceiveALoopProducedReply:
    """The bug that would have broken this feature on two of five channels.

    ``_deliver_success`` coerced the chat id with ``int()``. Telegram's ids are
    numeric so it worked, and retry_queue rows were telegram-only, so it was
    invisible. The moment the loop produces replies for EVERY gateway it stops
    being invisible:

        slack     "C123ABC"    -> int() RAISES        -> no reply at all
        whatsapp  "+15551234"  -> int() gives 15551234 -> a DIFFERENT address

    The second is the worse of the two: it does not fail, it silently sends
    someone else's answer somewhere else.
    """

    async def test_a_numeric_id_is_still_an_int_for_telegram(self) -> None:
        from stackowl.pipeline.retry_actuator import _native_chat_id

        assert _native_chat_id("72055773") == 72055773

    async def test_a_slack_channel_id_survives(self) -> None:
        from stackowl.pipeline.retry_actuator import _native_chat_id

        assert _native_chat_id("C123ABC") == "C123ABC"

    async def test_a_whatsapp_number_is_NOT_silently_rewritten(self) -> None:
        from stackowl.pipeline.retry_actuator import _native_chat_id

        assert _native_chat_id("+15551234") == "+15551234"
