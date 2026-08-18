"""A recovered task's answer must have somewhere to go.

FOUND 2026-08-18 while watching Bakir send a Gmail credentials file. The file
arrived and was read, the turn was recovered after an interruption, an answer of
222 characters was produced — and thrown away:

    [deliver] stream-miss: no durable fallback available — answer not delivered
    {"has_deliverer": true, "has_target": FALSE, "body_len": 222}

THE CAUSE. TaskRecovery builds its PipelineState carrying channel, owl_name, lane
and identity — everything except ``reply_target``. A recovered turn therefore has
no destination, ``_proactive_fallback`` declines ("a turn with no durable reply
target"), and the answer is discarded. Recovery existed to make sure work was not
lost on a crash, and was silently losing the ANSWER instead.

THE FIX USES WHAT THE LOOP ALREADY RECORDS. Since migration 0119 a chat task
carries ``destination`` — "telegram:72055773" — which is exactly the address the
reply needs. No new column, no second source of truth: the same field the delivery
rule already turns on.

A task with NO destination (a sweep, an internal sub-goal) still yields None, and
that is correct — nobody is waiting on it, so there is nothing to address.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.recovery import reply_target_for_task
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio


def _task(**over: object) -> DurableTask:
    base: dict = dict(task_id="t", goal="g", status="running")
    base.update(over)
    return DurableTask(**base)


class TestARecoveredAnswerIsAddressable:
    async def test_a_chat_destination_yields_its_address(self) -> None:
        """The exact case that lost Bakir's answer."""
        assert reply_target_for_task(
            _task(destination="telegram:72055773")
        ) == "72055773"

    async def test_a_slack_destination_keeps_its_native_id(self) -> None:
        """Slack channel ids are not numeric. Coercing them would raise, and this
        path must work for every gateway rather than only the numeric one."""
        assert reply_target_for_task(_task(destination="slack:C123ABC")) == "C123ABC"

    async def test_a_channel_only_destination_has_no_address(self) -> None:
        """CLI addresses its single terminal implicitly — there is no chat id to
        reply to, and inventing one would be worse than None."""
        assert reply_target_for_task(_task(destination="cli")) is None

    async def test_no_destination_yields_none(self) -> None:
        """A sweep or internal sub-goal has nobody waiting. None is correct, not a
        gap — and it is what keeps this from inventing a recipient."""
        assert reply_target_for_task(_task()) is None

    async def test_a_malformed_destination_does_not_raise(self) -> None:
        """Recovery runs after a crash. A parsing error here would turn a lost
        answer into a lost recovery."""
        for bad in ("", ":", "telegram:", ":72055773"):
            reply_target_for_task(_task(destination=bad))
