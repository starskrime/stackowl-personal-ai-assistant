"""DelegateTaskTool — hand a sub-task to a specialist owl and await its result.

Wraps the existing :class:`stackowl.owls.a2a_delegation.A2ADelegator` (the
Secretary→specialist request/spawn/await round-trip) with the E8 safety rails so
an owl can offload a focused sub-task to a better-suited specialist:

* **Depth backstop** — refuses (structured, never raises) once the current
  delegation depth reaches ``MAX_DELEGATION_DEPTH``. Defense-in-depth: the S0
  execution gate already withholds ``delegate_task`` from any sub-pipeline at
  ``delegation_depth > 0``, so by default delegation only fires at depth 0.
* **Width cap** — a per-``trace_id`` active-delegation counter refuses past
  ``MAX_CONCURRENT_DELEGATIONS`` in-flight for one turn, always decremented in
  ``finally`` so a failure never leaks a slot.
* **Structured timeout** — ``A2ADelegator.delegate`` returns ``""`` on
  timeout/failure; the tool converts that into a ``{"status":"timeout_or_empty"}``
  record so the model knows the specialist produced nothing (vs. inventing one).
* **Provenance footer** — the returned text carries a short footer naming the owl
  that handled it and that it was a delegated sub-run.

The tool reads :class:`stackowl.infra.trace.TraceContext` (depth/trace/session/
channel) — never ``PipelineState`` directly — and resolves its ``A2ADelegator``
off ``get_services()`` at execute time (never building one, so the governor/queue/
depth rails stay a single source of truth). Missing delegator, unresolvable
target, and delegate errors all surface as structured results (logged, B5); the
tool never raises. Severity ``write``; ``toolset_group`` ``agents``. Provenance:
HYBRID — StackOwl ``A2ADelegator`` round-trip + a minimal ported delegate
schema/safety shape (see ``_bmad-output`` research, not src).
"""

from __future__ import annotations

import threading
import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.interaction.cost_pause import gate_or_continue
from stackowl.owls.delegation_limits import (
    MAX_CONCURRENT_DELEGATIONS,
    MAX_DELEGATION_DEPTH,
)
from stackowl.pipeline.authz_compose import child_floor
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.agents.resolver import resolve_target_owl
from stackowl.tools.agents.results import (
    compose_sub_task,
    error_result,
    ok_result,
    provenance_footer,
    refusal_result,
)
from stackowl.tools.agents.schema import (
    DELEGATE_TASK_DESCRIPTION,
    DELEGATE_TASK_PARAMETERS,
)
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "agents"
_DEFAULT_CALLER = "secretary"


class DelegateTaskArgs(BaseModel):
    """Validated arguments for one ``delegate_task`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    to_owl: str | None = None
    role: str | None = None
    context: str | None = None


class DelegateTaskTool(Tool):
    """Delegate a focused sub-task to a specialist owl and return its result."""

    def __init__(self) -> None:
        """Construct the singleton tool.

        ``_active`` is a process-lifetime per-``trace_id`` in-flight counter for
        the width cap, guarded by a lock because concurrent ``delegate_task`` calls
        in the same turn (fan-out) mutate it from different tasks.
        """
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "delegate_task"

    @property
    def description(self) -> str:
        return DELEGATE_TASK_DESCRIPTION

    @property
    def parameters(self) -> dict[str, object]:
        return DELEGATE_TASK_PARAMETERS

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "delegate_task.execute: entry",
            extra={"_fields": {"has_to_owl": "to_owl" in kwargs, "has_role": "role" in kwargs}},
        )

        try:
            args = DelegateTaskArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "delegate_task.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return error_result(f"delegate_task: invalid arguments — {exc.errors()!r}", t0)

        ctx = TraceContext.get()
        depth = int(ctx.get("delegation_depth") or 0)
        # Width counter is keyed per turn; fall back to session (not a shared "")
        # so untraced turns don't contend for one global bucket (L1).
        trace_id = str(ctx.get("trace_id") or ctx.get("session_id") or "delegate-task")
        caller = self._caller_owl()

        # 2. DECISION — depth backstop (defense-in-depth; delegate NOT called).
        if depth >= MAX_DELEGATION_DEPTH:
            log.tool.warning(
                "delegate_task.execute: depth backstop — refusing",
                extra={"_fields": {"trace_id": trace_id, "depth": depth, "cap": MAX_DELEGATION_DEPTH}},
            )
            return refusal_result(
                t0, reason="depth_limit",
                detail=(
                    f"delegation depth limit reached ({depth} >= {MAX_DELEGATION_DEPTH}); "
                    "handle this sub-task yourself instead of delegating further."
                ),
            )

        services = get_services()
        delegator = services.a2a_delegator
        if delegator is None:
            log.tool.warning(
                "delegate_task.execute: no a2a_delegator wired — degraded",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return refusal_result(
                t0, reason="unavailable",
                detail="delegation is not available in this environment; handle the sub-task yourself.",
            )

        target = resolve_target_owl(
            registry=services.owl_registry, to_owl=args.to_owl, role=args.role, caller=caller,
        )
        if target is None:
            log.tool.warning(
                "delegate_task.execute: target unresolved — refusing",
                extra={"_fields": {"trace_id": trace_id, "to_owl": args.to_owl, "role": args.role}},
            )
            return refusal_result(
                t0, reason="unresolved_target",
                detail=(
                    "could not resolve a specialist owl for this sub-task "
                    f"(to_owl={args.to_owl!r}, role={args.role!r}); handle it yourself."
                ),
            )

        # E8-S0cost — soft per-turn cost pause via the ONE shared gate helper
        # (B2: same site as mixture_of_agents). BEFORE spending on a delegation, if
        # this turn's accumulated spend crossed the budget, it ASKS the user
        # (Continue/Stop). A "Stop" aborts here (structured refusal, no delegation
        # runs). The helper/guard fail OPEN + are interactive-only, so a background
        # run / under-budget turn / disabled feature proceeds unchanged.
        if not await gate_or_continue(services, action="delegation"):
            log.tool.info(
                "delegate_task.execute: cost pause — user chose Stop, aborting",
                extra={"_fields": {"trace_id": trace_id, "to": target}},
            )
            return refusal_result(
                t0, reason="cost_budget",
                detail=(
                    "stopped — this turn is over the per-turn cost budget and the "
                    "user chose not to continue; handle the sub-task yourself or stop."
                ),
            )

        # Width cap — refuse past MAX_CONCURRENT_DELEGATIONS in-flight for this trace.
        if not self._try_acquire(trace_id):
            log.tool.warning(
                "delegate_task.execute: width cap — refusing",
                extra={"_fields": {"trace_id": trace_id, "cap": MAX_CONCURRENT_DELEGATIONS}},
            )
            return refusal_result(
                t0, reason="width_limit",
                detail=(
                    f"too many concurrent delegations this turn (>= {MAX_CONCURRENT_DELEGATIONS}); "
                    "handle this sub-task yourself."
                ),
            )

        # 3. STEP — run the delegation round-trip; always release the width slot.
        try:
            return await self._run_delegation(
                delegator=delegator, args=args, caller=caller, target=target, depth=depth,
                trace_id=trace_id, session_id=str(ctx.get("session_id") or ""),
                channel=str(ctx.get("channel") or "internal"), t0=t0,
            )
        finally:
            self._release(trace_id)

    # ---------------------------------------------------------------- helpers

    async def _run_delegation(
        self,
        *,
        delegator: object,
        args: DelegateTaskArgs,
        caller: str,
        target: str,
        depth: int,
        trace_id: str,
        session_id: str,
        channel: str,
        t0: float,
    ) -> ToolResult:
        """Build parent_state, call delegate(), and shape the structured result."""
        sub_task = compose_sub_task(args.goal, args.context)
        parent_state = PipelineState(
            trace_id=trace_id or "delegate-task", session_id=session_id, input_text=sub_task,
            channel=channel, owl_name=caller, pipeline_step="dispatch", delegation_depth=depth,
            # E2-S2 delegation floor — clamp to parent EFFECTIVE bounds (owl ∩ ceiling)
            # — closes TOCTOU-delegation: a resumed parent whose owl was widened after
            # creation still clamps children to its persisted ceiling (FR35-runtime).
            # Back-compat: unstamped context ceiling (None) → owl bounds only (prior behavior).
            creation_ceiling=child_floor(
                caller, TraceContext.creation_ceiling(), get_services().owl_registry
            ),
        )
        log.tool.debug(
            "delegate_task._run_delegation: dispatching",
            extra={"_fields": {"trace_id": trace_id, "from": caller, "to": target, "depth": depth}},
        )
        try:
            result_text = await delegator.delegate(  # type: ignore[attr-defined]
                from_owl=caller, to_owl=target, sub_task=sub_task, parent_state=parent_state,
            )
        except Exception as exc:  # B5 — delegate is contracted not to raise; belt-and-braces.
            log.tool.error(
                "delegate_task._run_delegation: delegate raised — structured error",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "to": target}},
            )
            return ok_result(
                {"status": "error", "to_owl": target, "detail": str(exc)},
                t0, note=f"delegation to {target} failed",
            )

        if not result_text.strip():
            log.tool.warning(
                "delegate_task._run_delegation: empty/timeout result — structured status",
                extra={"_fields": {"trace_id": trace_id, "to": target}},
            )
            return ok_result(
                {"status": "timeout_or_empty", "to_owl": target, "result": ""},
                t0, note=f"{target} produced no result (timed out or returned nothing)",
            )

        record = {"status": "ok", "to_owl": target, "result": result_text + provenance_footer(target)}
        return ok_result(record, t0, note=f"{target} handled the sub-task")

    @staticmethod
    def _caller_owl() -> str:
        """The TRUE calling owl from TraceContext (propagated from state.owl_name),
        falling back to the Secretary origin. Reading the real caller avoids
        mis-attribution + a self-delegation loop when a non-secretary owl delegates."""
        owl = TraceContext.get().get("owl_name")
        return str(owl) if owl else _DEFAULT_CALLER

    def _try_acquire(self, trace_id: str) -> bool:
        """Increment the per-trace in-flight counter; refuse past the width cap."""
        with self._lock:
            current = self._active.get(trace_id, 0)
            if current >= MAX_CONCURRENT_DELEGATIONS:
                return False
            self._active[trace_id] = current + 1
            return True

    def _release(self, trace_id: str) -> None:
        """Decrement the per-trace in-flight counter; drop the key at zero."""
        with self._lock:
            current = self._active.get(trace_id, 0)
            if current <= 1:
                self._active.pop(trace_id, None)
            else:
                self._active[trace_id] = current - 1
