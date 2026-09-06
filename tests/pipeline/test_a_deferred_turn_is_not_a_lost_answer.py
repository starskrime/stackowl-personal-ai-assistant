"""A turn that owes nobody an answer must not be reported as losing one.

WHY THIS EXISTS, and the cost is the point: this warning sent a whole loop
chasing a data-loss bug that had not happened.

`[deliver] stream-miss: no durable fallback available — answer not delivered`
fired **180 times** in the retained window, **89 of them on user-facing owl lanes**
(`owl:secretary:recovery:*`, `owl:jobmarket:recovery:*`), each carrying
`has_deliverer: true, has_target: false` and a real body — 917 characters in one
case. Read at face value that is 89 answers computed and thrown away.

IT IS NOT. Traced line by line for `recover-task-c628837` on 2026-09-05:

    19:00:21  [loop] turn defers delivery and has no addressee —
              claiming NO destination rather than a channel name it could
              never deliver to
    19:03:47  [notifications] router.deliver: delivered
    19:12:05  [deliver] stream-miss: ... answer not delivered      <- this line
    19:12:39  [loop] task COMPLETE — its outcome reached its destination

The platform DECIDED there was no addressee, delivered through the job's own
path, and completed. All 89 user-facing misses have a `task COMPLETE` or a
no-addressee decision in the same window. Nothing was lost; the sentence was
false.

THE RULE ALREADY EXISTS, NAMED AND DOCUMENTED, IN THE SAME SUBSYSTEM.
`durable/turn_task.destination_for_turn` states it exactly — "deferred AND
unaddressed ⇒ owes nothing ⇒ None. Everything else keeps today's behaviour
byte-for-byte, including the deliberately loud case of an interactive turn that
lost its address — that is a real defect and must not be silently nulled into a
clean completion." The deliver step never asked it, though `state.defer_delivery`
is in scope at the branch.

SO THE LOUD CASE STAYS LOUD. This is not "log less": a turn that was supposed to
reach someone and did not is still a WARNING that says the answer was not
delivered. What changes is that a turn which by design owes nobody an answer
stops claiming a loss — because a warning that cries wolf is how a real one gets
ignored, and this one cost an hour before it was disproved.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.deliver import _proactive_fallback

pytestmark = pytest.mark.asyncio

_MISS = "[deliver] stream-miss: no durable fallback available — answer not delivered"


def _state(*, defer: bool) -> PipelineState:
    st = PipelineState(
        trace_id="recover-abc123",
        session_key="owl:jobmarket:recovery:task-abc123",
        input_text="run the daily goal",
        channel="cli",
        owl_name="jobmarket",
        pipeline_step="deliver",
        interactive=False,
        defer_delivery=defer,
    )
    return st.evolve(responses=(SimpleNamespace(content="the computed answer"),))


class _Deliverer:
    """Present but unusable — there is no address to send to."""


async def test_a_deferred_unaddressed_turn_does_not_claim_a_lost_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE DEFECT ITSELF. This turn owes nobody an answer — the loop said so at
    its start and the job delivered separately — so reporting a loss is false."""
    services = SimpleNamespace(proactive_deliverer=_Deliverer())

    with caplog.at_level(logging.DEBUG):
        out = await _proactive_fallback(_state(defer=True), services)  # type: ignore[arg-type]

    assert out is None
    losses = [r for r in caplog.records if r.getMessage() == _MISS]
    assert not losses, (
        "a turn that defers delivery and has no addressee was reported as having "
        "lost its answer — the same false alarm that cost a loop of investigation"
    )
    assert any("defer" in r.getMessage().lower() or "owes" in r.getMessage().lower()
               for r in caplog.records), (
        "the branch went silent instead of saying WHY there was nothing to send; "
        "silence and a false alarm are both worse than the truth"
    )


async def test_an_UNDEFERRED_turn_that_lost_its_address_is_still_LOUD(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE CONTROL THAT MATTERS. A turn that WAS supposed to reach someone and
    did not is a real defect. `turn_task` says so in its own words: it 'must not
    be silently nulled into a clean completion'. This fix must not silence it."""
    services = SimpleNamespace(proactive_deliverer=_Deliverer())

    with caplog.at_level(logging.DEBUG):
        out = await _proactive_fallback(_state(defer=False), services)  # type: ignore[arg-type]

    assert out is None
    losses = [r for r in caplog.records if r.getMessage() == _MISS]
    assert losses, "a genuinely lost answer stopped being reported — the fix went too far"
    assert losses[0].levelno >= logging.WARNING, (
        f"the real loss dropped below WARNING: {losses[0].levelname}"
    )


async def test_the_predicate_is_the_one_turn_task_already_defines() -> None:
    """ONE COPY OF THE RULE. `destination_for_turn` is where 'deferred AND
    unaddressed ⇒ owes nothing' is written down; a second, drifting copy of that
    judgement is the defect shape this repo pays for most often."""
    from stackowl.pipeline.durable.turn_task import destination_for_turn

    assert destination_for_turn(
        channel="cli", reply_target=None, defer_delivery=True
    ) is None, "the shared rule no longer says a deferred unaddressed turn owes nothing"
    assert destination_for_turn(
        channel="telegram", reply_target="72055773", defer_delivery=True
    ), "an ADDRESSED deferred turn still owes a delivery"
