"""W1.T3 — persistence-check fallback judge tier + all-turns enforcement.

The deliver-vs-giveup judge used to be a nested closure inside execute(), gated to
interactive depth-0 turns and backed by a SINGLE judge provider whose failure was
silently accepted. This suite drives the extracted module-level factory
:func:`build_persistence_check`:

  * a primary-judge failure falls through to a DIFFERENT fallback provider before
    failing open;
  * a primary "delivered" verdict still returns no directive (unchanged behaviour);
  * the checker is built even for a delegated / non-interactive turn (gate dropped).

The fake judges mirror the real provider interface ``judge_delivery`` calls —
``provider.complete(messages, model="")`` returning an object with a ``.content``
strict-JSON string (see ``stackowl.pipeline.persistence.judge_delivery``).
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import build_persistence_check


class _Completion:
    def __init__(self, content: str) -> None:
        self.content = content


class _RaisingJudge:
    """Primary judge model that errors on every call."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        raise RuntimeError("judge down")


class _DeliveredJudge:
    """Judge that rules 'delivered' -> no directive."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion('{"delivered": true, "reason": "ok"}')


class _GaveUpJudge:
    """Judge that rules 'gave up' -> directive."""

    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion('{"delivered": false, "reason": "stub"}')


@pytest.fixture
def fake_services() -> StepServices:
    # No provider_registry needed — tests inject primary/fallback explicitly.
    return StepServices()


def _state(trace_id: str, text: str, *, interactive: bool, depth: int) -> PipelineState:
    return PipelineState(
        trace_id=trace_id,
        session_id="s-1",
        input_text=text,
        channel="cli",
        owl_name="secretary",
        pipeline_step="execute",
        interactive=interactive,
        delegation_depth=depth,
    )


@pytest.fixture
def fake_state() -> PipelineState:
    return _state("t-interactive", "do the hard task", interactive=True, depth=0)


@pytest.fixture
def fake_state_delegated() -> PipelineState:
    return _state(
        "t-delegated", "do the delegated sub-task", interactive=False, depth=2
    )


@pytest.mark.asyncio
async def test_fallback_used_when_primary_raises(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    check = build_persistence_check(
        fake_state, fake_services, primary=_RaisingJudge(), fallback=_GaveUpJudge()
    )
    directive = await check("a give-up draft", ["browser_browse(failed)"])
    assert directive == PERSISTENCE_DIRECTIVE  # fallback consulted, ruled give-up


@pytest.mark.asyncio
async def test_primary_delivered_no_directive(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    check = build_persistence_check(
        fake_state, fake_services, primary=_DeliveredJudge()
    )
    assert await check("a fine answer", ["shell(ok)"]) is None


@pytest.mark.asyncio
async def test_both_judges_raise_fail_open(
    fake_state: PipelineState, fake_services: StepServices
) -> None:
    # Primary AND fallback raise -> final fail-open (None), turn never blocked.
    check = build_persistence_check(
        fake_state, fake_services, primary=_RaisingJudge(), fallback=_RaisingJudge()
    )
    assert await check("a give-up draft", ["browser_browse(failed)"]) is None


@pytest.mark.asyncio
async def test_checker_built_for_delegated_turn(
    fake_state_delegated: PipelineState, fake_services: StepServices
) -> None:
    # Gate dropped: non-interactive / depth>0 STILL gets a checker (not None).
    check = build_persistence_check(
        fake_state_delegated, fake_services, primary=_DeliveredJudge()
    )
    assert check is not None
    assert await check("a fine answer", ["shell(ok)"]) is None
