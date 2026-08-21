"""The loop says so when pending work exists that it will never claim.

WHY THIS EXISTS, and it is not hypothetical. On 2026-08-20 the live table held 387
task rows filed under an owner the loop is not bound to — 72 of them ``pending``,
the oldest a day and a half old, every one past its ``next_attempt_at``. The loop
had been running the whole time and had never touched them, because ``claimable``
carries ``WHERE owner_id = ?`` and ``TaskLoop`` is constructed with no owner.

The writer that produced them has been fixed (see
``test_a_task_belongs_to_a_principal.py``). This is the other half, and the more
important one: NOTHING NOTICED. loop.py's own docstring already names the state —
"work accumulates in a table nobody is draining, and nothing reports it" — and
calls it worse than having no loop at all. The platform was in exactly that state
and reported nothing for a day and a half.

The standing rule (feedback_always_self_healing) is to ask what notices when
something degrades silently, and to build the actuator rather than file the debt.
So the loop counts, at start, the pending rows outside its own owner and says so.

DELIBERATELY A COUNT AND A WARNING, NOT A RESCUE. Claiming those rows would drive
work the loop was never given — 72 conversation-summary CHECKPOINT records fed to
the ReAct kernel as goals, one model call each. Whether the task table should be
multi-owner at all is ESC-17's question and Bakir's to answer; this makes it a
question with a NUMBER attached instead of an invisible landfill.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _Store:
    """The loop's store surface, narrowed to what start() touches."""

    def __init__(self, unreachable: int = 0, raises: bool = False,
                 healed: int = 0) -> None:
        self._unreachable = unreachable
        self._raises = raises
        self._healed = healed
        self.asked = 0
        self.healed_calls = 0

    async def revive_undelivered_failures(self, *, limit: int = 50) -> int:
        return 0

    async def count_pending_for_other_owners(self) -> int:
        self.asked += 1
        if self._raises:
            raise RuntimeError("the count query blew up")
        return self._unreachable

    async def heal_unreachable_owners(self, *, limit: int = 500) -> int:
        """The double grew a method because the loop now HEALS rather than counts —
        see test_unreachable_work_heals_itself.py. A double that lags the real surface
        is this platform's second recurring defect, so it tracks."""
        self.healed_calls += 1
        if self._raises:
            raise RuntimeError("the heal query blew up")
        return self._healed


async def _start(store: _Store):
    from stackowl.pipeline.durable.loop import TaskLoop

    loop = TaskLoop(store=store, runner=_never_runs, tick_seconds=3600)
    await loop.start()
    await loop.stop()
    return loop


async def _never_runs(task: object) -> str:  # pragma: no cover — never claimed here
    raise AssertionError("this test never dispatches a task")


class TestUnreachableWorkIsAnnounced:
    async def test_pending_rows_under_another_owner_are_reported(
        self, caplog
    ) -> None:
        """The exact live number, so the assertion is about a real state."""
        store = _Store(unreachable=72)
        with caplog.at_level("WARNING"):
            await _start(store)

        hits = [r for r in caplog.records if "no loop could claim" in r.message]
        assert hits, f"silent; records were {[r.message for r in caplog.records]}"

    async def test_a_clean_table_says_nothing(self, caplog) -> None:
        """The warning has to mean something. A loop that warns on every start is
        one an operator stops reading, which is how the next landfill hides."""
        store = _Store(unreachable=0)
        with caplog.at_level("WARNING"):
            await _start(store)

        assert not [r for r in caplog.records if "no loop could claim" in r.message]
        assert store.asked == 1, "the count must actually run, not be skipped"

    async def test_a_failing_count_never_stops_the_loop(self, caplog) -> None:
        """The property everything else rests on is that the loop starts. A
        bookkeeping query that can prevent that is worse than the rows it counts —
        the same reasoning as the undelivered-failure sweep beside it."""
        store = _Store(raises=True)
        with caplog.at_level("ERROR"):
            loop = await _start(store)

        assert loop.worker_id
        assert any("could not count" in r.message or "starting anyway" in r.message for r in caplog.records)
