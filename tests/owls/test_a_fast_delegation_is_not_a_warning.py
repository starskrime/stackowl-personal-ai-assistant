"""A delegation that SUCCEEDED must not log a failure.

MEASURED 2026-08-21, while Bakir was mid-conversation. The log carried a rising
count of:

    [a2a-delegator] delegate: specialist task did not finish in time

3 on 08-19, 3 on 08-20, 7 on 08-21 — and the wording reads like the delegation
failed. It did not. Correlating each one with the ``delegate: exit`` that follows:

    12 of 14 returned status=ok
    ALL 14 returned duration_ms=0

Which is the whole explanation. The warning sits AFTER ``self._a2a_queue.receive``
has already returned, so at that line the reply is always in hand — this branch
cannot indicate a delegation failure, by construction. ``duration_ms=0`` means the
specialist had already replied before the parent began waiting, so its task object
was still unwinding when the parent checked. The warning therefore fires precisely
when delegation is FASTEST.

That is the shape this platform keeps paying for: a WARNING on the happy path. It is
the same defect as the delivery-proof one fixed earlier in the same session — an
alarm that cannot distinguish a real problem from ordinary operation is not an alarm,
and it competes for attention with the ones that are.

WHY NOT SIMPLY RAISE THE 1s. Because no wait is correct: the parent already has what
it asked for. How long the child's coroutine takes to unwind afterwards is
bookkeeping, and bookkeeping does not belong at WARNING. The observation is kept —
at DEBUG, saying what actually happened — so it can still be found if a child ever
does hang, without crying wolf on every fast turn.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

pytestmark = pytest.mark.asyncio


async def _slow_unwind() -> None:
    """A specialist that has already sent its reply but is still finishing."""
    await asyncio.sleep(5)


class TestTheHappyPathIsQuiet:
    async def test_a_still_unwinding_specialist_is_not_a_warning(
        self, caplog
    ) -> None:
        """The measured case: reply in hand, task object not yet done."""
        from stackowl.owls import a2a_delegation

        task = asyncio.create_task(_slow_unwind())
        try:
            with caplog.at_level(logging.DEBUG):
                await a2a_delegation.settle_specialist_task(
                    task, trace_id="t", to_owl="Brain", timeout=0.01,
                )
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
            assert not warnings, (
                f"warned on a successful delegation: {[r.message for r in warnings]}"
            )
        finally:
            task.cancel()

    async def test_it_is_still_observable_at_debug(self, caplog) -> None:
        """Quiet is not silent. A child that genuinely hangs must still be findable —
        the fix is the LEVEL and the wording, not the observation."""
        from stackowl.owls import a2a_delegation

        task = asyncio.create_task(_slow_unwind())
        try:
            with caplog.at_level(logging.DEBUG):
                await a2a_delegation.settle_specialist_task(
                    task, trace_id="t", to_owl="Brain", timeout=0.01,
                )
            assert any("unwinding" in r.message.lower() for r in caplog.records), (
                f"observation lost entirely: {[r.message for r in caplog.records]}"
            )
        finally:
            task.cancel()

    async def test_an_already_finished_specialist_logs_nothing(self, caplog) -> None:
        """The overwhelmingly common case must cost nothing at all."""
        from stackowl.owls import a2a_delegation

        async def _done() -> None:
            return None

        task = asyncio.create_task(_done())
        await task

        with caplog.at_level(logging.DEBUG):
            await a2a_delegation.settle_specialist_task(
                task, trace_id="t", to_owl="Brain", timeout=0.01,
            )

        assert caplog.records == []

    async def test_it_never_raises(self) -> None:
        """The parent already HAS its answer. A bookkeeping helper that could throw
        would turn a delivered delegation into a failed turn — the exact inversion
        this file exists to prevent."""
        from stackowl.owls import a2a_delegation

        async def _boom() -> None:
            raise RuntimeError("the child exploded on its way out")

        task = asyncio.create_task(_boom())
        await a2a_delegation.settle_specialist_task(
            task, trace_id="t", to_owl="Brain", timeout=0.5,
        )
