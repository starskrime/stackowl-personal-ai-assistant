"""LangGraphBackend stamps state.task_id/durable_owner_id into TraceContext — D1 §8.1.

Mirrors ``test_asyncio_backend_durable_scope`` for the sibling backend. The graph
is built from ``PIPELINE_STEPS`` at construction time, so we patch the list to a
single probe step BEFORE instantiating the backend. ``use_memory_checkpoint=True``
keeps the test hermetic (no sqlite / heavy deps).
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

_observed: dict[str, object] = {}


async def _probe_step(state: PipelineState) -> PipelineState:
    _observed["task_id"] = TraceContext.get()["task_id"]
    _observed["owner"] = TraceContext.durable_owner_id()
    return state


async def test_langgraph_backend_propagates_durable_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    import stackowl.pipeline.backends.langgraph_backend as mod

    _observed.clear()
    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    backend = LangGraphBackend(services=StepServices(), use_memory_checkpoint=True)
    state = PipelineState(
        trace_id="tr", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=False,
        task_id="child-9", durable_owner_id="owner-z",
    )
    try:
        await backend.run(state)
    finally:
        await backend.shutdown()
    assert _observed["task_id"] == "child-9"
    assert _observed["owner"] == "owner-z"


async def test_langgraph_backend_no_durable_scope_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-open: a state without durable scope leaves the trace fields None."""
    import stackowl.pipeline.backends.langgraph_backend as mod

    _observed.clear()
    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    backend = LangGraphBackend(services=StepServices(), use_memory_checkpoint=True)
    state = PipelineState(
        trace_id="tr", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=False,
    )
    try:
        await backend.run(state)
    finally:
        await backend.shutdown()
    assert _observed["task_id"] is None
    assert _observed["owner"] is None
