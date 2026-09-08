"""The sibling branch that was left behind when its twin was fixed.

`tests/pipeline/test_a_deferred_turn_is_not_a_lost_answer.py` fixed one of
`deliver.run`'s no-delivery exits: a turn that owes nobody stopped claiming a
loss, because "a warning that cries wolf is how a real one gets ignored, and this
one cost an hour before it was disproved". The branch TWENTY LINES ABOVE it in the
same file was not touched, and it has the same defect:

    if registry is None:
        log.gateway.warning(
            "[pipeline] deliver: no registry in services — discarding responses",

MEASURED 2026-09-08 across the twelve retained logs: **153 events, every single
one on a `shadow-validate-*` session**, at a steady 10-15 a day for the whole
window — the only WARNING in the live census that fires at a constant rate rather
than in an incident burst.

THE DISCARD IS A DESIGNED ISOLATION GUARANTEE, and `shadow_validator.py`'s module
docstring is where it is written down: a replay "never delivers a message
(``stream_registry=None`` + ``interactive=False`` + no ``reply_target`` ⇒
``deliver.run`` discards the response)". That absent registry is not a fault. It
is the mechanism by which a pre-commit DNA replay is guaranteed to have no side
effects on a live user.

THE COST IS MEASURED, NOT ASSUMED. This line has been investigated and dismissed
by TWO separate loops, and both wrote it down in `progress.yml`:
`SHADOWGATE_2026_09_01` opened on it as one of two new signals, and a later
round-0 census listed "39 [pipeline] deliver: no registry in services —
DISCARDING response" among its top numbers and concluded "the 39 are ALL
`shadow-validate-*` sessions — not user turns. Discarding them is correct."
Diagnosed twice, left in place twice, and it would have cost the next loop the
same time.

WHY IT HAPPENED. `stream_registry=None` carries two different facts and no way to
tell them apart: a lane BUILT without a registry so it cannot deliver by design,
and a live interactive turn whose services were misassembled so it cannot deliver
by accident. Absence-by-design and absence-by-fault were the same value, so the
branch could not classify, and it defaulted to alarm — which is the worst of both,
because it cries wolf ten times a day AND could not have raised its voice for the
case that matters.

The state already declares which one it is. `interactive` is the second member of
the shadow validator's own isolation triple, and it means exactly "a human is
waiting on a stream". So the branch asks it.

THE LOUD CASE STAYS LOUD, deliberately, and that is the whole reason this is not
"log less": an INTERACTIVE turn that reaches this branch has a person waiting and
nowhere to write, and it is still a WARNING that says the response was discarded.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


def _state(*, interactive: bool) -> PipelineState:
    """A turn that produced an answer and has no live stream to write it to.

    `defer_delivery=False` and `delegation_depth=0` deliberately: both of those
    exits sit ABOVE the branch under test and would carry every assertion here
    without the branch existing at all — the "satisfied by a case the old code
    handled too" trap.
    """
    st = PipelineState(
        trace_id="shadow-validate-deadbeef",
        session_key="shadow-validate-deadbeef",
        input_text="replayed input",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        interactive=interactive,
    )
    # THE REAL ``ResponseChunk``, not a namespace with a ``content`` attribute.
    # The first draft used the latter and died on ``c.is_floor`` in the floor
    # check above the branch — a double that had stopped resembling the thing it
    # stands in for, before it could test anything.
    return st.evolve(responses=(ResponseChunk(
        content="the replayed answer",
        is_final=False,
        chunk_index=0,
        trace_id=st.trace_id,
        owl_name=st.owl_name,
    ),))


async def _run_with_no_registry(state: PipelineState) -> None:
    """`deliver.run` against scratch services — exactly the shadow validator's."""
    token = set_services(StepServices(stream_registry=None))
    try:
        await deliver.run(state)
    finally:
        reset_services(token)


async def _levels_for(state: PipelineState, caplog: pytest.LogCaptureFixture) -> list[int]:
    caplog.set_level(logging.DEBUG)
    await _run_with_no_registry(state)
    return [r.levelno for r in caplog.records if "deliver" in r.message and "registry" in r.message]


async def test_a_replay_lane_owes_no_delivery_and_says_so_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE 153 EVENTS. A non-interactive lane with no registry lost nothing."""
    levels = await _levels_for(_state(interactive=False), caplog)

    assert levels, "the branch stopped saying anything at all — a silent discard is worse"
    assert max(levels) < logging.WARNING, (
        "a replay lane's designed absence of a stream is still reported as a fault; "
        "this exact line has been diagnosed and dismissed by two separate loops"
    )


async def test_an_interactive_turn_with_no_stream_is_still_LOUD(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE CASE THAT MATTERS. A person is waiting and there is nowhere to write."""
    levels = await _levels_for(_state(interactive=True), caplog)

    assert levels and max(levels) >= logging.WARNING, (
        "an interactive turn reached deliver with no stream registry and the "
        "platform said nothing louder than INFO — that is a real lost answer"
    )


async def test_the_two_cases_are_distinguishable_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Not just the level — the RECORD has to carry which case it was.

    A reader grepping these logs (this programme's own instruments do) must be
    able to count the fault cases without re-deriving the lane from the session
    key, which is how both prior investigations were forced to work.
    """
    caplog.set_level(logging.DEBUG)
    await _run_with_no_registry(_state(interactive=False))
    quiet = [r for r in caplog.records if "registry" in r.message]
    caplog.clear()
    await _run_with_no_registry(_state(interactive=True))
    loud = [r for r in caplog.records if "registry" in r.message]

    assert quiet and loud
    assert quiet[0].message != loud[0].message, (
        "both cases emit the same sentence, so no query can separate the designed "
        "absence from the real one"
    )
    fields = getattr(quiet[0], "_fields", {})
    assert "interactive" in fields, (
        "the record does not carry the fact the branch decided on; a reader has to "
        "guess the lane from its session key, which is what cost two loops"
    )


async def test_the_response_is_still_not_delivered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE ISOLATION GUARANTEE MUST SURVIVE THIS CHANGE.

    `shadow_validator` depends on `deliver.run` discarding — a replay that
    delivered would push a pre-commit DNA experiment at a real user. Changing how
    the discard is REPORTED must not change that it discards, and a test that only
    read the log level could not tell.
    """
    state = _state(interactive=False)
    out = await _levels_for(state, caplog) and None
    assert out is None
    token = set_services(StepServices(stream_registry=None))
    try:
        result = await deliver.run(state)
    finally:
        reset_services(token)
    assert result.responses == state.responses, "deliver mutated a replay's responses"
