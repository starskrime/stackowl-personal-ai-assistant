""""No vetoes" is only evidence if abstentions are counted too.

D02.5 ("Acceptance authority / measured success") carries five
``no_change_needed`` stages, and its closing argument is::

    zero_vetoes_in_production_and_why_that_is_not_alarming: "No veto log lines
    across four days. The module logs only at ERROR on its failure path, so
    silence is the expected shape of 'nothing went wrong'."

MEASURED 2026-08-30, and the silence is NOT that shape. `_verify_turn_acceptance`
has three exits and only one of them speaks:

  * `criteria is None`        -> returns None, LOGS NOTHING
  * `verdict.accepted is None`-> returns the verdict, LOGS NOTHING
  * a definite verdict        -> INFO "[acceptance] normal-turn verdict"
  * an exception              -> WARNING

Across the entire retained log that INFO line appears exactly THREE times, and all
three are `objgoal-*` traces — objective sub-goals, not the chat turns the line is
named for. So on ordinary turns the authority ABSTAINS, and "no vetoes" conflates
"judged and passed" with "never formed an opinion".

That is the difference between a falsifiable claim and an unfalsifiable one, and it
is the failure this repo already paid for once: an acceptance check whose evidence
line could never appear, so no volume of traffic could ever close it. Here it is
worse than a wrong LEVEL — the line does not exist on the abstaining paths at all,
so the DENOMINATOR is invisible.

WHAT THIS DOES NOT CHANGE. Not one line of the acceptance DECISION. Abstaining when
there are no criteria is correct — a turn that declared no outcome should not be
judged against one. The claim being fixed is the evidential one: after this, "no
vetoes" can be read against "how many turns were actually judged", and D02.5's
closure becomes checkable instead of assumed.
"""

from __future__ import annotations

import logging

import pytest


class _State:
    trace_id = "t-abstain"
    expected_outcome = None
    responses = ()
    tool_calls = ()


@pytest.mark.asyncio
async def test_it_says_so_when_there_are_NO_criteria(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The commonest exit, and the one that produced four days of silence."""
    from stackowl.pipeline.backends import shared

    async def _no_criteria(state, services):  # noqa: ANN001,ANN202
        return None

    shared._derive_turn_acceptance = _no_criteria  # type: ignore[assignment]

    with caplog.at_level(logging.INFO):
        verdict = await shared._verify_turn_acceptance(_State(), None, 0.0)

    assert verdict is None, "the decision changed; only the evidence should have"
    records = [r for r in caplog.records if "acceptance" in r.getMessage().lower()]
    assert records, (
        "the authority abstained and said nothing — 'no vetoes' cannot be read "
        "against a denominator that is never written down"
    )
    assert any("abstain" in r.getMessage().lower() for r in records)


@pytest.mark.asyncio
async def test_the_abstention_is_at_INFO_not_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Production runs at INFO. A DEBUG line is no evidence at all — the exact
    failure D08.1 paid for, where an acceptance check sat open for days because
    its only evidence line could never appear."""
    from stackowl.pipeline.backends import shared

    async def _no_criteria(state, services):  # noqa: ANN001,ANN202
        return None

    shared._derive_turn_acceptance = _no_criteria  # type: ignore[assignment]

    with caplog.at_level(logging.DEBUG):
        await shared._verify_turn_acceptance(_State(), None, 0.0)

    acc = [r for r in caplog.records if "abstain" in r.getMessage().lower()]
    assert acc
    assert all(r.levelno >= logging.INFO for r in acc), (
        f"the abstention logs below INFO: {[r.levelname for r in acc]}"
    )


@pytest.mark.asyncio
async def test_the_reason_distinguishes_the_two_abstentions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """"No criteria" and "indeterminate verdict" are different states.

    Collapsing them would rebuild the very ambiguity this fixes — a single
    "abstained" count cannot tell 'nothing to judge' from 'judged, no opinion'.
    """
    from stackowl.pipeline.backends import shared

    async def _no_criteria(state, services):  # noqa: ANN001,ANN202
        return None

    shared._derive_turn_acceptance = _no_criteria  # type: ignore[assignment]

    with caplog.at_level(logging.INFO):
        await shared._verify_turn_acceptance(_State(), None, 0.0)

    fields = {}
    for r in caplog.records:
        if "abstain" in r.getMessage().lower():
            fields = getattr(r, "_fields", {})
    assert fields.get("reason"), f"the abstention carries no reason: {fields}"
    assert fields.get("trace_id") == "t-abstain"
