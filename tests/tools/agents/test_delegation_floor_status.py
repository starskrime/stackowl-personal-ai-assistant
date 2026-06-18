"""W4.T16 — a delegated child that FLOORED must surface an HONEST FAILURE status.

The never-empty floor (W2) guarantees a hard-failed turn still produces TEXT
(a localized last-resort response). The invariant guarded here: that floor text
must NOT make a failed delegated child look successful. The delegation result
builder OWNS the status — it keys off the child's terminal ``state.errors``
(non-empty → honest failure), so a floored child (responses non-empty BUT errors
non-empty) maps to a NON-``ok`` status carrying the floor text, NEVER a fake ``ok``.

The parent can then re-route around the failed child instead of trusting the
floor apology as a real answer.

Two layers are locked:

* the child-state → ``A2AResult`` mapping in ``A2ADelegator._run_specialist``
  (the source of truth: status is governor-decided from ``state.errors``);
* the honest-failure ``ToolResult`` builders (``success=False`` + non-empty text).
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator, A2AResult
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.tools.agents.results import (
    honest_irrelevant_result,
    honest_offtopic_write_result,
    honest_uncertain_result,
)

_FLOOR_TEXT = (
    "I hit a hard failure and could not complete this; please try again shortly."
)


def _parent(**kw: Any) -> PipelineState:
    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="go",
        channel="cli",
        owl_name="secretary",
        pipeline_step="dispatch",
        **kw,
    )


def _floor_chunk() -> ResponseChunk:
    """A never-empty FLOOR chunk: text is present but it marks a hard failure."""
    return ResponseChunk(
        content=_FLOOR_TEXT,
        is_final=True,
        chunk_index=0,
        trace_id="t",
        owl_name="scout",
        is_floor=True,
    )


# ---------------------------------------------------------------------------
# Layer 1 — the child-state → A2AResult mapping (source of truth for status).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegated_floor_is_honest_failure_not_fake_ok() -> None:
    """A floored child (responses non-empty + errors non-empty) → honest failure.

    Drives the REAL ``_run_specialist`` → ``delegate`` round-trip: the only fake
    is the backend, which returns a terminal state that is FLOORED (it carries a
    floor response chunk AND a hard error). The result builder must NOT read the
    presence of response text as success — status is keyed off ``state.errors``.
    """
    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=5.0)

    floored_final = _parent().evolve(
        owl_name="scout",
        responses=(_floor_chunk(),),
        errors=("execute: RuntimeError: boom",),
    )

    async def _fake_governor(backend: Any, sub_state: PipelineState) -> PipelineState:
        return floored_final

    with patch.object(deleg, "_run_under_governor", new=_fake_governor):
        res = await deleg.delegate(
            from_owl="secretary",
            to_owl="scout",
            sub_task="do the thing",
            parent_state=_parent(),
        )

    assert isinstance(res, A2AResult)
    # honest failure — NOT a fake ok (status owned by the builder, keyed off errors)
    assert res.status != "ok"
    assert res.status == "child_error"
    # the floor text rides up so the parent can see what the child produced
    assert _FLOOR_TEXT in res.content
    # the hard-error detail rode up too (untrusted, sanitized)
    assert "boom" in res.child_detail


@pytest.mark.asyncio
async def test_floor_text_present_but_no_errors_is_ok() -> None:
    """Control: text present and NO errors → ``ok`` (the floor only flips status
    via the error channel, never via response-presence). This proves the mapping
    keys off ``errors``, not off ``responses``/text content."""
    q = A2AQueue()
    deleg = A2ADelegator(a2a_queue=q, services=StepServices(), timeout_seconds=5.0)

    clean_final = _parent().evolve(
        owl_name="scout",
        responses=(
            ResponseChunk(
                content="a real answer",
                is_final=True,
                chunk_index=0,
                trace_id="t",
                owl_name="scout",
            ),
        ),
        errors=(),
    )

    async def _fake_governor(backend: Any, sub_state: PipelineState) -> PipelineState:
        return clean_final

    with patch.object(deleg, "_run_under_governor", new=_fake_governor):
        res = await deleg.delegate(
            from_owl="secretary",
            to_owl="scout",
            sub_task="do the thing",
            parent_state=_parent(),
        )

    assert res.status == "ok"
    assert res.content == "a real answer"


# ---------------------------------------------------------------------------
# Layer 2 — the honest-failure ToolResult builders the parent surfaces.
# ---------------------------------------------------------------------------


def test_honest_failure_builders_are_success_false_with_text() -> None:
    """Every honest-failure builder → ``success=False`` AND carries non-empty text.

    These are the parent-facing terminals for a re-routed / halted child. The
    builder OWNS status; the floor only contributed text, so a hard-failed child
    is reported as an honest failure (not a masked success) while still surfacing
    a message the parent can act on.
    """
    t0 = time.monotonic()
    for res in (
        honest_uncertain_result("scout", t0),
        honest_offtopic_write_result("scout", t0),
        honest_irrelevant_result(t0),
    ):
        assert res.success is False
        assert res.output or res.error  # non-empty text rides up
