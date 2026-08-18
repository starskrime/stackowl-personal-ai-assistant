"""The loop is not just for chat — an agent's own scheduled work is a task too.

BAKIR, 2026-08-18: *"loop should not be only for telegram, it should be for all
gateway and all actions what agents done."*

THE GATEWAY HALF WAS ALREADY TRUE and is asserted here so it stays that way: the
chat ingress lives in the orchestrator's shared ``_intake``/``_dispatch_turn``,
which takes a generic ``_IntakeAdapter``. Telegram dominates the live rows only
because it is the only channel that has had traffic. A change that made the ingress
channel-specific would be a regression nothing else would catch.

THE AGENT HALF WAS NOT. ``goal_execution`` — the agent doing its own scheduled work
— already created durable task rows, but with ``trigger_kind``, ``destination`` and
``achievement`` all NULL. The live rows read:

    status='completed'  delivered_at=NULL

which is a task claiming completion with no proof its outcome reached anyone. That
is precisely the overclaim shape the loop exists to prevent, sitting inside the
agent's own actions rather than in a chat turn.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.agent_task import (
    complete_agent_task,
    describe_job_destination,
)

pytestmark = pytest.mark.asyncio


class _Store:
    def __init__(self, *, boom: bool = False) -> None:
        self.delivered: list[tuple[str, str]] = []
        self.stamped: list[tuple[str, dict]] = []
        self._boom = boom

    async def mark_delivered(self, task_id: str, *, result: str) -> None:
        if self._boom:
            raise RuntimeError("db gone")
        self.delivered.append((task_id, result))


class _Job:
    def __init__(self, channels=(), addresses=()) -> None:
        self.target_channels = list(channels)
        self.target_addresses = list(addresses)


class TestAScheduledActionHasADestination:
    async def test_a_job_with_a_channel_and_address(self) -> None:
        assert describe_job_destination(_Job(("telegram",), ("72055773",))) == (
            "telegram:72055773"
        )

    async def test_a_job_with_a_channel_but_no_address(self) -> None:
        """A single-terminal channel addresses itself implicitly — still a real
        destination, so completion is still checkable."""
        assert describe_job_destination(_Job(("cli",), ())) == "cli"

    async def test_a_job_with_NO_targets_has_no_destination(self) -> None:
        """Honest None rather than an invented one. A maintenance job that delivers
        nothing must not be held to a delivery it was never asked to make — that
        would dead-letter every sweep on the platform."""
        assert describe_job_destination(_Job()) is None

    async def test_several_channels_are_all_recorded(self) -> None:
        """A broadcast lands in more than one place; the destination must say so
        rather than silently picking the first."""
        d = describe_job_destination(_Job(("telegram", "slack"), ("72055773",)))

        assert d is not None
        assert "telegram" in d and "slack" in d


class TestCompletionStillMeansDelivered:
    async def test_a_delivered_answer_completes_the_task(self) -> None:
        store = _Store()

        await complete_agent_task(
            store, task_id="task-1", result="Here is your morning brief.",
            delivery_status="completed",
        )

        assert store.delivered == [("task-1", "Here is your morning brief.")]

    async def test_an_UNDELIVERABLE_answer_does_not_complete_it(self) -> None:
        """The row the live data showed: completed with delivered_at NULL. The
        answer existed and reached nobody, so the task did NOT achieve its goal and
        must stay open for the loop."""
        store = _Store()

        await complete_agent_task(
            store, task_id="task-1", result="a brief nobody received",
            delivery_status="undeliverable",
        )

        assert store.delivered == []

    async def test_a_PARTIAL_delivery_does_not_complete_it(self) -> None:
        """Some channels failed. Marking complete would strand the rest silently."""
        store = _Store()

        await complete_agent_task(
            store, task_id="task-1", result="x", delivery_status="partial",
        )

        assert store.delivered == []

    async def test_an_empty_result_does_not_complete_it(self) -> None:
        store = _Store()

        await complete_agent_task(
            store, task_id="task-1", result="   ", delivery_status="completed",
        )

        assert store.delivered == []


class TestItNeverCostsTheJob:
    async def test_no_store_is_a_noop(self) -> None:
        await complete_agent_task(None, task_id="t", result="x",
                                  delivery_status="completed")

    async def test_a_raising_store_does_not_break_the_job(self) -> None:
        """The answer HAS been delivered by the time this runs. Failing to record
        it must not turn a delivered brief into a failed job."""
        await complete_agent_task(_Store(boom=True), task_id="t", result="x",
                                  delivery_status="completed")


class TestTheGatewayHalfStaysUniversal:
    async def test_the_chat_ingress_is_channel_agnostic(self) -> None:
        """Bakir: the loop must not be telegram-only. The ingress takes whatever
        channel the turn arrived on; this fails if someone hard-codes one.
        """
        from stackowl.pipeline.durable.turn_task import _destination

        for channel, chat, expected in (
            ("telegram", 72055773, "telegram:72055773"),
            ("slack", "C123", "slack:C123"),
            ("discord", "987", "discord:987"),
            ("whatsapp", "+1555", "whatsapp:+1555"),
            ("cli", None, "cli"),
        ):
            assert _destination(channel, chat) == expected
