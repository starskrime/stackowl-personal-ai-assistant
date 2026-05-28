"""PipelineState — immutable pipeline execution state with evolve()."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from stackowl.pipeline.streaming import ResponseChunk


class ToolCall(BaseModel, frozen=True):
    """A record of a single tool invocation within the pipeline."""

    tool_name: str
    args: dict[str, Any]
    result: str | None
    error: str | None
    duration_ms: float


class PipelineState(BaseModel, frozen=True):
    """Immutable snapshot of pipeline execution state.

    Mutation is via evolve(**kwargs) — returns a new instance.
    """

    trace_id: str
    session_id: str
    input_text: str
    channel: str
    owl_name: str
    pipeline_step: str
    responses: tuple[ResponseChunk, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    memory_context: str | None = None
    errors: tuple[str, ...] = ()
    # Per-pipeline-step elapsed time in milliseconds, keyed by step name.
    # Populated by the backend's step loop; consumed by the outcome-capture
    # helper at end-of-run. Frozen tuple-of-tuples to keep PipelineState
    # immutable (pydantic frozen=True forbids mutable dicts).
    step_durations: tuple[tuple[str, float], ...] = ()

    def evolve(self, **kwargs: Any) -> PipelineState:
        """Return a new PipelineState with the given fields updated."""
        return self.model_copy(update=kwargs)
