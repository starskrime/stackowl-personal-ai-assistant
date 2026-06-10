"""AsyncioBackend stamps state.task_id/durable_owner_id into TraceContext — D1 §8.1."""

from __future__ import annotations

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

_observed: dict[str, object] = {}


async def _probe_step(state: PipelineState) -> PipelineState:
    _observed["task_id"] = TraceContext.get()["task_id"]
    _observed["owner"] = TraceContext.durable_owner_id()
    return state


async def test_backend_propagates_durable_scope(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    backend = AsyncioBackend(services=StepServices())
    state = PipelineState(
        trace_id="tr", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=False,
        task_id="child-9", durable_owner_id="owner-z",
    )
    await backend.run(state)
    assert _observed["task_id"] == "child-9"
    assert _observed["owner"] == "owner-z"


async def test_backend_no_durable_scope_is_none(monkeypatch) -> None:  # noqa: ANN001
    """Fail-open: a state without durable scope leaves the trace fields None."""
    import stackowl.pipeline.backends.asyncio_backend as mod

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    _observed.clear()
    backend = AsyncioBackend(services=StepServices())
    state = PipelineState(
        trace_id="tr", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=False,
    )
    await backend.run(state)
    assert _observed["task_id"] is None
    assert _observed["owner"] is None
