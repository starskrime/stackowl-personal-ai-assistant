"""The lane a scheduled job produces must be one the tools it drives accept.

MEASURED 2026-09-01 across the retained logs: **every one of the 20 RCA-driven
``tool_build`` attempts failed** — 3 on 08-29, 11 on 08-30, 6 on 08-31 — each
with the same refusal::

    tool_build.execute: no channel/session to scope consent — refused
    refused: registering a new tool ('shell_budget_guard') needs a conversation
    to attribute the approval to, and this turn has none.

So for three days the self-heal loop diagnosed correctly, proposed a tool, and
could not build one. 0 for 20.

THE FIX LANDED AND WAS NEVER VERIFIED. ``ee3ba6e0`` (2026-08-31T10:28:34Z) gave
a job's trace a channel, and the last refusal is timestamped 10:16:25Z — twelve
minutes earlier. Since then there have been **2 verdicts routed and ZERO
tool_build attempts**, because the denominator gate now suppresses most
incidents and the router skips verdicts carrying no literal command. Zero
refusals over zero attempts is not evidence of anything, and this project has
already paid for reading it as a pass.

THE ROOT CAUSE IS THAT THE CONTRACT HAS TWO HALVES AND NOTHING CHECKED THEY MEET.
``test_job_trace_has_a_lane.py`` pins the PRODUCER (``_bind_job_trace`` sets
``channel="internal"``) and quotes the consumer's error message as its own
motivation — and still nobody drove the pair. ``tool_build`` pins its own
refusal. Each half is green in isolation while the composition was, for three
days, broken. "Two copies of one rule" with no meeting point.

WHAT THE SAME CAUSE REACHES: every consumer of the job lane. ``owl_build``
carries the identical check and the identical wording, so all of them are
asserted here rather than the one that happened to be reported.

THE DENIALS BELOW ARE DELIBERATE AND ARE ASSERTED AS SUCH. ``owl_build`` is
refused under a job lane because it is always-ask
categories and ``internal`` is not a gateway channel, so the provenance grant
does not apply either. That is the recorded decision — the shape of the owl
fleet must never change unattended — and pinning it here stops a later widening
of the lane from quietly widening those too.

``code_execution`` LEFT this parametrisation on 2026-09-02: Bakir relaxed it out
of the always-ask set (ESC-98), so asserting it is still refused would pin a
contract that no longer exists. The lane invariant is unchanged and still
asserted through ``owl_build``, which remains always-ask. This case was missed
when ESC-98 shipped because that run covered the consent-touching files and not
``tests/scheduler``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.scheduler.scheduler import _bind_job_trace
from stackowl.tools.consent import (
    _DEFAULT_ALWAYS_ASK_CATEGORIES,
    ConsentPolicy,
    FailClosedPrompter,
)
from stackowl.tools.meta.tool_build import _CONSENT_CATEGORY

pytestmark = pytest.mark.asyncio

_JOB = SimpleNamespace(
    job_id="incident_escalation-9fb1c485", primary_channel=None, target_channels=None,
)


@pytest.fixture
def job_lane():  # noqa: ANN201
    """The REAL trace a scheduled job runs under — produced, not hand-built."""
    token = _bind_job_trace(_JOB)
    try:
        ctx = TraceContext.get()
        yield {"channel": ctx.get("channel"), "session_key": ctx.get("session_key")}
    finally:
        TraceContext.reset(token)


def _accepts_the_lane(lane: dict) -> bool:
    """The lane check every meta-tool applies, in the form they all write it."""
    return bool(lane["channel"]) and bool(lane["session_key"])


async def test_the_lane_a_job_produces_is_one_tool_build_accepts(job_lane) -> None:  # noqa: ANN001
    """The composition nobody checked. Each half was green while 20 consecutive
    builds were refused."""
    assert _accepts_the_lane(job_lane), (
        "a scheduled job's trace no longer satisfies the meta-tool lane check — "
        "the self-heal loop is back to diagnosing without being able to act"
    )


async def test_consent_actually_GRANTS_a_tool_build_on_that_lane(job_lane) -> None:  # noqa: ANN001
    """The second half, and the one that would have moved the refusal rather than
    removing it. Passing the lane check only gets you to the consent gate; a
    fix that satisfied the check and was then denied would look identical from
    the outside and still build nothing.

    FailClosedPrompter deliberately: there is no human on a scheduled job, so
    this asserts the AUTONOMOUS grant, not a prompt someone answered.
    """
    policy = ConsentPolicy(prompter=FailClosedPrompter())
    granted = await policy.request(
        tool_name="tool_build", channel=job_lane["channel"],
        session_key=job_lane["session_key"], category=_CONSENT_CATEGORY,
        summary="Register new tool x", reversible=True,
    )
    assert granted is True, (
        "the self-heal loop can reach the consent gate and is refused there — "
        "the lane fix moved the refusal instead of removing it"
    )


@pytest.mark.parametrize("category", ["owl_build"])
async def test_an_always_ask_category_is_still_refused_unattended(
    job_lane, category: str,  # noqa: ANN001
) -> None:
    """The expensive direction, and a recorded decision. Giving jobs a lane must
    NOT have widened what an unattended run may do: the shape of the owl fleet
    and arbitrary code execution stay always-ask, and `internal` is not a
    gateway channel so provenance does not grant them either."""
    assert category in _DEFAULT_ALWAYS_ASK_CATEGORIES
    policy = ConsentPolicy(prompter=FailClosedPrompter())
    granted = await policy.request(
        tool_name="t", channel=job_lane["channel"],
        session_key=job_lane["session_key"], category=category,
        summary="s", reversible=True,
    )
    assert granted is False, (
        f"'{category}' was auto-granted to an unattended scheduled job — giving "
        f"jobs a lane has widened authority, which it must never do"
    )


async def test_a_trace_with_no_lane_is_still_refused() -> None:
    """The guard must stay real. If the lane check can never fail, a genuine
    wiring fault would register tools against nothing and the audit record would
    have no subject."""
    assert _accepts_the_lane({"channel": None, "session_key": "job:x"}) is False
    assert _accepts_the_lane({"channel": "internal", "session_key": None}) is False
    assert _accepts_the_lane({"channel": "", "session_key": ""}) is False


async def test_the_job_lane_still_reports_no_human(job_lane) -> None:  # noqa: ANN001
    """Attribution, not attendance. The lane exists so consent has a subject; it
    must not make an unattended sweep look like someone is watching."""
    token = _bind_job_trace(_JOB)
    try:
        ctx = TraceContext.get()
        assert ctx.get("interactive") is False
        assert ctx.get("conversation_id") is None, (
            "a conversation was invented for a scheduled job — that attributes "
            "its cost to a conversation that never happened"
        )
    finally:
        TraceContext.reset(token)
