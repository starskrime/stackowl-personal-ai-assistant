"""FR-11/FR-12 shared seam — LangGraphBackend acceptance-verification parity.

Regression guard for the FR-13 gap this commit closes: before FR-12,
``LangGraphBackend.run()`` never called ``_verify_turn_acceptance`` — it called
``_capture_outcome`` with no ``acceptance=`` kwarg, so a LangGraph-run turn that
declared an ``expected_outcome`` reality refuted was always captured as a
trustworthy success (unlike ``AsyncioBackend``, which already verified). This
test drives a real ``LangGraphBackend.run()`` end to end and asserts the
captured outcome reflects the refutation — proving ``_verify_turn_acceptance``
genuinely ran, not just that the helper function works in isolation (mirrors
``tests/pipeline/test_backend_acceptance.py``'s AsyncioBackend setup).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import stackowl.pipeline.backends.shared as shared
from stackowl.infra import recovery_context
from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _snapshot_consequential
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


async def _probe_step(state: PipelineState) -> PipelineState:
    return state


class _FakeStore:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, _db: Any) -> None:
        pass

    async def record(self, **kwargs: Any) -> None:
        _FakeStore.last_kwargs = kwargs


async def test_langgraph_backend_verifies_acceptance_and_refutes_false_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackowl.pipeline.backends.langgraph_backend as mod

    _FakeStore.last_kwargs = {}
    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    monkeypatch.setattr(shared, "TaskOutcomeStore", _FakeStore)

    missing = tmp_path / "never-written"
    services = StepServices(db_pool=object())  # type: ignore[arg-type]
    backend = LangGraphBackend(services=services, use_memory_checkpoint=True)
    state = PipelineState(
        trace_id="t-parity", session_id="s-parity", input_text="do the thing",
        channel="cli", owl_name="secretary", pipeline_step="",
        expected_outcome=ExpectedOutcome(kind="artifact", artifact_dir=str(missing)),
        responses=(ResponseChunk(
            content="all done!", is_final=True, chunk_index=0,
            trace_id="t-parity", owl_name="secretary",
        ),),
    )
    try:
        await backend.run(state)
    finally:
        await backend.shutdown()

    assert _FakeStore.last_kwargs["success"] is False
    assert _FakeStore.last_kwargs["failure_class"] == shared._UNACHIEVED_EFFECT_CLASS


async def test_asyncio_backend_captures_recovered_via_substitution_past_context_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-6 Task 6 fix (round 2, reviewer-caught regression) — a REAL reset-
    ordering proof, not a hand-built TaskOutcome row.

    A probe step fires ``recovery_context.record_recovery(kind="substitution",
    ...)`` — mirroring exactly what ``execute.py``'s real substitution path does
    — and stamps the snapshot via the REAL ``execute._snapshot_consequential``
    (not reimplemented). ``AsyncioBackend.run()``'s own ``finally`` then calls
    ``recovery_context.reset()`` BEFORE ``_capture_outcome`` runs (the exact
    ordering the reviewer found broken: a direct ``recovery_context.get_recovery()``
    read inside ``_capture_outcome`` would ALWAYS see ``()`` here, since the
    ContextVar has already been reset by the time it runs). This test proves
    ``_capture_outcome`` instead reads the value off immutable
    ``state.recovered_via_substitution`` (stamped BEFORE the reset), so the
    captured outcome row genuinely carries the real bridged tool name.
    """
    import stackowl.pipeline.backends.asyncio_backend as mod

    async def _probe_step(state: PipelineState) -> PipelineState:
        recovery_context.record_recovery(
            kind="substitution", failed="broken_tool",
            recovered_via="sibling_tool", user_visible=True,
        )
        return _snapshot_consequential(state)

    _FakeStore.last_kwargs = {}
    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    monkeypatch.setattr(shared, "TaskOutcomeStore", _FakeStore)

    services = StepServices(db_pool=object())  # type: ignore[arg-type]
    backend = AsyncioBackend(services=services)
    state = PipelineState(
        trace_id="t-recov", session_id="s-recov", input_text="do the thing",
        channel="cli", owl_name="secretary", pipeline_step="",
    )
    await backend.run(state)

    assert _FakeStore.last_kwargs["recovered_via_tool"] == "broken_tool"
