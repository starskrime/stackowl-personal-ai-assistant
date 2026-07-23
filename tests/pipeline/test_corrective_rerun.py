"""Delivery-gate corrective re-run — agentic self-correction, not just confession.

Before: a draft rejected by the retrieval-intent overclaim trigger or the
grounding gate was REPLACED with an honest floor ("I didn't actually look this
up — want me to?"), pushing the fix back onto the user. Now the gate feeds the
rejection reason back to the model and re-runs the full pipeline ONCE
(RetryActuator.run_corrective — tools included, so the correction can actually
retrieve); the corrected answer is adopted only if the child's own gates clear
it. Every failure path keeps the legacy floor byte-identically. Bounded:
corrective_replay=True on the child means a correction is never corrected.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.delivery_gate import (
    surface_grounding_gate,
    surface_overclaim_gate,
)
from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t-corr",
        session_id="s",
        input_text="what's the latest on GOOGL?",
        channel="cli",
        owl_name="o",
        pipeline_step="execute",
        turn_made_progress=True,
        no_progress_tools=(),
        consequential_failures=(),
        consequential_snapshot_taken=False,
        delivered_successes=(),
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(content: str, *, is_floor: bool = False) -> ResponseChunk:
    return ResponseChunk(
        content=content, is_final=False, chunk_index=0,
        trace_id="t-corr", owl_name="o", is_floor=is_floor,
    )


class _FakeBackend:
    """Backend double: records the corrective state, returns a scripted child."""

    def __init__(self, child: PipelineState | None = None, raise_exc: bool = False) -> None:
        self.child = child
        self.raise_exc = raise_exc
        self.ran: list[PipelineState] = []

    async def run(self, state: PipelineState) -> PipelineState:
        self.ran.append(state)
        if self.raise_exc:
            raise RuntimeError("provider down")
        assert self.child is not None
        return self.child


def _actuator(backend: _FakeBackend) -> RetryActuator:
    return RetryActuator(
        backend=backend,  # type: ignore[arg-type]
        channel_registry=None,  # type: ignore[arg-type]
        retry_store=None,  # type: ignore[arg-type]
    )


def _clean_child(text: str) -> PipelineState:
    return _state(
        trace_id="t-corr-fix",
        responses=(
            ResponseChunk(
                content=text, is_final=False, chunk_index=0,
                trace_id="t-corr-fix", owl_name="o",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_retrieval_overclaim_corrected_in_turn() -> None:
    backend = _FakeBackend(child=_clean_child("GOOGL closed at $195 (source: reuters.com)."))
    token = set_services(StepServices(retry_actuator=_actuator(backend)))
    try:
        state = _state(
            responses=(_draft("GOOGL is probably around $180 or so."),),
            requires_retrieval=True,
            intent_class="standard",
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is False
    assert not any(c.is_floor for c in result.responses)
    assert "reuters.com" in result.responses[0].content
    # The corrective child got the rejection reason + the bound flags.
    corrective_state = backend.ran[0]
    assert corrective_state.corrective_replay is True
    assert corrective_state.retry_replay is True
    assert corrective_state.defer_delivery is True
    assert "rejected" in corrective_state.input_text
    # Workstream B — the child's retry_lineage_id must match the PARENT's
    # trace_id (not the child's own derivative trace_id, "t-corr-fix"), so
    # the retry ledger correlates the correction with the turn it's fixing.
    assert corrective_state.retry_lineage_id == "t-corr"


@pytest.mark.asyncio
async def test_retrieval_overclaim_falls_back_to_floor_when_child_floors() -> None:
    floored_child = _state(
        trace_id="t-corr-fix",
        responses=(_draft("still couldn't", is_floor=True),),
    )
    backend = _FakeBackend(child=floored_child)
    token = set_services(StepServices(retry_actuator=_actuator(backend)))
    try:
        state = _state(
            responses=(_draft("GOOGL is probably around $180 or so."),),
            requires_retrieval=True,
            intent_class="standard",
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is True
    assert result.responses[0].is_floor is True


@pytest.mark.asyncio
async def test_corrective_replay_never_corrects_a_correction() -> None:
    backend = _FakeBackend(child=_clean_child("corrected"))
    token = set_services(StepServices(retry_actuator=_actuator(backend)))
    try:
        state = _state(
            responses=(_draft("GOOGL is probably around $180 or so."),),
            requires_retrieval=True,
            intent_class="standard",
            corrective_replay=True,  # this turn IS already a correction
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert backend.ran == []  # no recursion — floored directly
    assert result.overclaim_blocked is True


@pytest.mark.asyncio
async def test_backend_exception_keeps_floor() -> None:
    backend = _FakeBackend(raise_exc=True)
    token = set_services(StepServices(retry_actuator=_actuator(backend)))
    try:
        state = _state(
            responses=(_draft("GOOGL is probably around $180 or so."),),
            requires_retrieval=True,
            intent_class="standard",
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is True
    assert result.responses[0].is_floor is True


@pytest.mark.asyncio
async def test_grounding_no_sources_corrected_in_turn() -> None:
    backend = _FakeBackend(
        child=_clean_child("Verified: see https://reuters.com/googl (retrieved).")
    )
    token = set_services(StepServices(retry_actuator=_actuator(backend)))
    try:
        # Draft cites a URL but the turn fetched nothing → fabricated by definition.
        state = _state(
            responses=(_draft("Big news! https://totally-made-up.example/googl"),),
        )
        result = await surface_grounding_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is False
    assert not any(c.is_floor for c in result.responses)
    assert "reuters.com" in result.responses[0].content
    assert backend.ran[0].corrective_replay is True


@pytest.mark.asyncio
async def test_no_actuator_wired_keeps_legacy_floor(monkeypatch: Any) -> None:
    token = set_services(StepServices())  # no retry_actuator
    try:
        state = _state(
            responses=(_draft("GOOGL is probably around $180 or so."),),
            requires_retrieval=True,
            intent_class="standard",
        )
        result = await surface_overclaim_gate(state)
    finally:
        reset_services(token)
    assert result.overclaim_blocked is True
    assert result.responses[0].is_floor is True
