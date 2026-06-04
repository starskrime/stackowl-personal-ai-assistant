"""PipelineState — immutable pipeline execution state with evolve()."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import Message


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
    # True when a user is present on the originating channel and can answer a
    # mid-turn clarify question. FAIL-CLOSED: defaults to False — a human is
    # assumed ABSENT unless a user-facing channel (CLI/Telegram/etc.) EXPLICITLY
    # sets interactive=True for a real user turn. cron/scheduler, parliament, and
    # A2A sub-pipelines ride this False default so a clarify call default-denies
    # (returns its ABORT sentinel) instead of parking a coroutine with no one to
    # answer it. A forgotten flag therefore degrades safely to "clarify
    # unavailable" rather than faking a human presence.
    interactive: bool = False
    # Recursion depth of this (sub-)pipeline in the delegation tree. 0 for a
    # top-level user turn; incremented by one each time A2ADelegator spawns a
    # specialist child (see _run_specialist). Carried across evolve() like every
    # other field. The child-toolset exclusion gates on depth>0 (PRIMARY
    # fork-bomb cap), and the S1 delegate_task tool refuses past
    # MAX_DELEGATION_DEPTH (defense-in-depth).
    delegation_depth: int = 0
    # ID of the durable task this pipeline turn belongs to, or None for an
    # ephemeral (non-durable) turn. Carried across evolve() like every other
    # field. Consumed by the langgraph backend to isolate per-task checkpoints
    # (thread_id = "session::task_id") so a durable task's resume replays its own
    # checkpoint, not a sibling turn's. Additive — default None preserves the
    # exact prior behavior for every non-durable turn.
    task_id: str | None = None
    # ID of an in-flight clarify question awaiting a user answer for this run.
    # The Event itself lives in the (out-of-band) clarify registry — a frozen
    # model cannot hold an asyncio.Event — so only the id is carried in state.
    pending_clarify_id: str | None = None
    responses: tuple[ResponseChunk, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    memory_context: str | None = None
    # Real prior conversation turns (user/assistant), oldest-first. Populated by
    # the classify step from staged conversation rows and threaded into the
    # provider messages array by execute. Empty for the first turn / non-chat
    # pipelines. RC-C fix.
    history: tuple[Message, ...] = ()
    # Final assembled system prompt (owl persona + DNA directives + memory
    # blocks). Built by the assemble step; consumed by execute. None until
    # assemble runs. RC-B fix.
    system_prompt: str | None = None
    errors: tuple[str, ...] = ()
    # Per-pipeline-step elapsed time in milliseconds, keyed by step name.
    # Populated by the backend's step loop; consumed by the outcome-capture
    # helper at end-of-run. Frozen tuple-of-tuples to keep PipelineState
    # immutable (pydantic frozen=True forbids mutable dicts).
    step_durations: tuple[tuple[str, float], ...] = ()

    def evolve(self, **kwargs: Any) -> PipelineState:
        """Return a new PipelineState with the given fields updated."""
        return self.model_copy(update=kwargs)
