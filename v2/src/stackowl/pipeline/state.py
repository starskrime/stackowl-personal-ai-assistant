"""PipelineState — immutable pipeline execution state with evolve()."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from stackowl.authz.bounds import BoundsSpec
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
    # Per-turn delivery target for fan-out channels (e.g. a Telegram chat_id),
    # threaded from IngressMessage.chat_id by the orchestrator at construction.
    # The deliver step stamps it onto every outgoing ResponseChunk so a turn's
    # output routes back to ITS OWN chat under concurrency — never the shared
    # _last_chat_id (overwritten by every newer inbound update). CLI turns leave
    # it None; the adapter then resolves the destination itself. Carried across
    # evolve() like every other field; default None keeps every non-Telegram turn
    # byte-for-byte unchanged.
    # String targets are for Slack (channel id / thread_ts); int for Telegram chat_id.
    reply_target: int | str | None = None
    # Recursion depth of this (sub-)pipeline in the delegation tree. 0 for a
    # top-level user turn; incremented by one each time A2ADelegator spawns a
    # specialist child (see _run_specialist). Carried across evolve() like every
    # other field. The child-toolset exclusion gates on depth>0 (PRIMARY
    # fork-bomb cap), and the S1 delegate_task tool refuses past
    # MAX_DELEGATION_DEPTH (defense-in-depth).
    delegation_depth: int = 0
    # Owl-name ancestry of the current delegation (governor-stamped, model-untouchable).
    # Powers cycle detection (refuse if a target is already in the chain). len() == delegation_depth.
    delegation_chain: tuple[str, ...] = ()
    # ID of the durable task this pipeline turn belongs to, or None for an
    # ephemeral (non-durable) turn. Carried across evolve() like every other
    # field. Consumed by the langgraph backend to isolate per-task checkpoints
    # (thread_id = "session::task_id") so a durable task's resume replays its own
    # checkpoint, not a sibling turn's. Additive — default None preserves the
    # exact prior behavior for every non-durable turn.
    task_id: str | None = None
    # E2-S2/S3 — the task-scoped authorization fields.
    # ENFORCEMENT formula: effective = owl.bounds(now) ∩ creation_ceiling
    #
    # creation_ceiling — a snapshot of the owl's bounds taken at DURABLE task
    # creation, persisted on the task row. It narrows nothing on a normal run
    # (owl ∩ owl = owl); its sole effect is on RESUME after the owl's bounds were
    # widened mid-task, where owl.bounds(now) ∩ creation_ceiling clamps to the
    # narrower historical set (resume-monotonicity / TOCTOU ratchet). None for a
    # non-durable turn — no clamp. A missing ceiling is therefore NEVER
    # global-unrestricted, because owl.bounds(now) always remains a factor.
    creation_ceiling: BoundsSpec | None = None
    # task_envelope — the least-privilege-per-task slot. NOT enforced (E2-S3).
    # Drives presentation restrict_to and drift telemetry only. ALWAYS None in S2;
    # the E2-S3 preflight planner fills it with a goal-derived (tighter) spec.
    # Carried here now so S3 populates an existing field rather than re-threading.
    task_envelope: BoundsSpec | None = None
    # B2 durable-react — additive carriers for the durable activation in the
    # execute step. ALL default None so a non-durable turn (task_id is None) is
    # byte-for-byte unchanged. `durable_owner_id` is the owning principal whose
    # ledger/store rows this drive writes (falls back to DEFAULT_PRINCIPAL_ID when
    # None). The `durable_resume_*` trio is populated later by the B4 checkpoint
    # reconstruction and forwarded verbatim into complete_with_tools; in B2 they
    # are merely carried across evolve() like every other field.
    durable_owner_id: str | None = None
    durable_resume_messages: list[dict[str, Any]] | None = None
    durable_resume_tool_calls: list[dict[str, Any]] | None = None
    durable_resume_iteration: int | None = None
    # B2 durable-react — PARK signal. Set True when a durable drive hit a
    # DurableReplayUncertain (an `intent` ledger row without a matching commit:
    # a prior attempt may have half-run a side effect, so the guard refuses to
    # re-run it). This is a STRUCTURED park signal distinct from a transient
    # failure: the B3 router reads `durable_parked` to decide park-vs-retry,
    # rather than string-matching state.errors. Additive — default False keeps
    # every non-durable turn (and every durable turn that did not park)
    # byte-for-byte unchanged.
    durable_parked: bool = False
    # ID of an in-flight clarify question awaiting a user answer for this run.
    # The Event itself lives in the (out-of-band) clarify registry — a frozen
    # model cannot hold an asyncio.Event — so only the id is carried in state.
    pending_clarify_id: str | None = None
    responses: tuple[ResponseChunk, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    memory_context: str | None = None
    # Query embedding computed once in classify (semantic only), forwarded so assemble
    # can score owned skills without re-embedding. None = no usable relevance signal. Story B.
    query_embedding: tuple[float, ...] | None = None
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
