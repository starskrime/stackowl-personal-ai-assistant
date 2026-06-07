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
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.interaction.cost_pause import gate_or_continue
from stackowl.owls.delegation_limits import (
    MAX_CONCURRENT_DELEGATIONS,
    MAX_DELEGATION_ATTEMPTS_PER_TURN,
    MAX_DELEGATION_DEPTH,
)
from stackowl.pipeline.authz_compose import child_floor, resolve_owl_bounds
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.agents.resolver import resolve_target
from stackowl.tools.agents.results import (
    child_error_result,
    compose_sub_task,
    cycle_result,
    error_result,
    ok_result,
    provenance_footer,
    recovered_result,
    refusal_result,
    target_not_found_result,
    truncated_result,
)
from stackowl.tools.agents.schema import (
    DELEGATE_TASK_DESCRIPTION,
    DELEGATE_TASK_PARAMETERS,
)
from stackowl.tools.base import Tool, ToolManifest, ToolResult

if TYPE_CHECKING:
    from stackowl.owls.a2a_delegation import A2AResult

_TOOLSET_GROUP = "agents"
_DEFAULT_CALLER = "secretary"


def _normalize_subtask(s: str) -> str:
    """Collapse whitespace only.  NO casefold — sub_tasks can be code/paths where case is semantic."""
    return " ".join(s.split())

_SIDE_EFFECT_SEVERITIES: frozenset[str] = frozenset({"write", "consequential"})


def _can_side_effect(owl_name: str) -> bool:
    """True if the owl could run a write/consequential tool.

    Used by the delegation ladder to decide whether a child's work can be safely
    re-delegated — a write-capable child may have already acted, so re-delegation
    is not safe.

    Conservative: if unverifiable (no registry, unknown owl, or unrestricted
    bounds), returns True (treat as side-effecting).
    """
    svc = get_services()
    bounds = resolve_owl_bounds(owl_name, svc.owl_registry)
    if bounds is None or bounds.tools is None:
        # Unknown owl or unrestricted bounds → could side-effect → conservative True
        return True
    treg = svc.tool_registry
    if treg is None:
        # Cannot verify severities → conservative True
        return True
    for name in bounds.tools:
        tool = treg.get(name)
        if tool is not None and tool.manifest.action_severity in _SIDE_EFFECT_SEVERITIES:
            return True
    return False


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

        ``_attempts`` is a cumulative per-``trace_id`` counter for the global
        per-turn attempt budget (``MAX_DELEGATION_ATTEMPTS_PER_TURN``). It bounds
        all delegate() calls (initial + retries + fallbacks) within one turn so a
        crafted prompt cannot walk an unbounded delegation tree. Bounded to 256
        entries to prevent an unbounded leak across turns.
        """
        self._active: dict[str, int] = {}
        self._attempts: dict[str, int] = {}
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

        resolution = resolve_target(
            registry=services.owl_registry, to_owl=args.to_owl, role=args.role, caller=caller,
        )
        if resolution.reason == "target_not_found":
            log.tool.warning(
                "delegate_task.execute: to_owl not found — structured status",
                extra={"_fields": {"trace_id": trace_id, "to_owl": args.to_owl}},
            )
            return target_not_found_result(t0, to_owl=args.to_owl or "")
        if resolution.name is None:
            log.tool.warning(
                "delegate_task.execute: target unresolved — refusing",
                extra={"_fields": {"trace_id": trace_id, "to_owl": args.to_owl, "role": args.role}},
            )
            return refusal_result(
                t0, reason="unresolved_target",
                detail="could not resolve a specialist; handle it yourself.",
            )
        target = resolution.name

        # Cycle check — BEFORE cost pause and width-acquire (no slot leak).
        chain = tuple(TraceContext.get().get("delegation_chain") or ())
        if target in chain or target == caller:
            log.tool.warning(
                "delegate_task.execute: cycle detected — refusing pre-slot",
                extra={"_fields": {"trace_id": trace_id, "target": target, "chain": chain}},
            )
            return cycle_result(t0, target=target, chain=(*chain, caller))

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

    # Statuses that are transient failures worth retrying/falling back on.
    _RETRIABLE = frozenset({"timeout", "empty", "child_error"})

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
        """Build parent_state once, then run the bounded recovery ladder.

        Ladder: initial attempt → retry-once (same target) → fallback to secretary.

        The SAME ``parent_state`` (and therefore the SAME ``child_floor``) is
        reused for every attempt — no re-computation, no escalation of bounds.
        The width slot is held by the ``execute`` finally-block; this method NEVER
        calls ``_try_acquire``. Depth is NOT incremented between attempts.

        1. ENTRY — log inputs.
        2. DECISION — build parent_state once; inner _attempt() checks budget.
        3. STEP — run up to 3 delegate() calls (initial + retry + fallback).
        4. EXIT — return shaped ToolResult.
        """
        # 1. ENTRY
        log.tool.debug(
            "delegate_task._run_delegation: entry",
            extra={"_fields": {"trace_id": trace_id, "from": caller, "to": target, "depth": depth}},
        )

        # 2. DECISION — build parent_state ONCE; reused for all attempts.
        sub_task = compose_sub_task(args.goal, args.context)
        # D2 in-ladder dedup memo — local to this _run_delegation call, discarded on return.
        # Key: (target_owl, normalized_sub_task). Hit only on status=="ok" to avoid
        # suppressing a retry that should surface a different terminal status.
        from stackowl.owls.a2a_delegation import A2AResult as _A2AResult  # local avoids circular dep
        memo: dict[tuple[str, str], _A2AResult] = {}
        chain = tuple(TraceContext.get().get("delegation_chain") or ())
        parent_state = PipelineState(
            trace_id=trace_id or "delegate-task", session_id=session_id, input_text=sub_task,
            channel=channel, owl_name=caller, pipeline_step="dispatch", delegation_depth=depth,
            delegation_chain=chain,
            # E2-S2 delegation floor — clamp to parent EFFECTIVE bounds (owl ∩ ceiling).
            # Reused for ALL attempts so the fallback cannot escalate the creation_ceiling.
            creation_ceiling=child_floor(
                caller, TraceContext.creation_ceiling(), get_services().owl_registry
            ),
        )
        log.tool.debug(
            "delegate_task._run_delegation: parent_state built, beginning ladder",
            extra={"_fields": {"trace_id": trace_id, "target": target, "chain": chain}},
        )

        async def _attempt(to_owl: str) -> A2AResult | ToolResult:
            """Charge one attempt unit then call delegate(); returns A2AResult (or a
            ToolResult on the belt-and-braces exception path).

            D2 dedup: check memo BEFORE _charge_attempt so a replay is free.
            """
            key = (to_owl, _normalize_subtask(sub_task))
            cached = memo.get(key)
            if cached is not None and cached.status == "ok":
                # D2 dedup: never re-run a child that already succeeded in this ladder.
                log.tool.debug(
                    "delegate_task._attempt: memo hit — reusing ok result",
                    extra={"_fields": {"trace_id": trace_id, "to_owl": to_owl}},
                )
                return cached
            if not self._charge_attempt(trace_id):
                log.tool.warning(
                    "delegate_task._run_delegation: attempt budget exhausted — short-circuit",
                    extra={"_fields": {"trace_id": trace_id, "cap": MAX_DELEGATION_ATTEMPTS_PER_TURN}},
                )
                from stackowl.owls.a2a_delegation import A2AResult  # local import avoids circular dep
                return A2AResult(status="refused", resolved_owl=to_owl)
            try:
                res: _A2AResult = await delegator.delegate(  # type: ignore[attr-defined]
                    from_owl=caller, to_owl=to_owl, sub_task=sub_task, parent_state=parent_state,
                )
            except Exception as exc:  # B5 — delegate is contracted not to raise; belt-and-braces.
                log.tool.error(
                    "delegate_task._run_delegation: delegate raised — structured error",
                    exc_info=exc,
                    extra={"_fields": {"trace_id": trace_id, "to": to_owl}},
                )
                return ok_result(
                    {"status": "error", "to_owl": to_owl, "detail": str(exc)},
                    t0, note=f"delegation to {to_owl} failed",
                )
            memo[key] = res  # D2: store result; future same-key ok hits will use this.
            return res

        # 3. STEP — initial attempt.
        result = await _attempt(target)
        # If _attempt() caught an exception it already returned a ToolResult.
        if isinstance(result, ToolResult):
            return result

        log.tool.debug(
            "delegate_task._run_delegation: initial attempt done",
            extra={"_fields": {"trace_id": trace_id, "status": getattr(result, "status", "?")}},
        )

        # Retry-once on retriable failure (same target, same parent_state, same floor).
        if getattr(result, "status", None) in self._RETRIABLE:
            log.tool.debug(
                "delegate_task._run_delegation: retry-once",
                extra={"_fields": {"trace_id": trace_id, "target": target, "prev_status": result.status}},
            )
            result = await _attempt(target)
            if isinstance(result, ToolResult):
                return result

        # Fallback to secretary if still failing and conditions allow.
        if getattr(result, "status", None) in self._RETRIABLE:
            registry = get_services().owl_registry
            secretary = registry.secretary_name() if registry is not None else None
            # Skip self-fallback and in-chain fallback.
            if (
                secretary is not None
                and secretary != caller
                and secretary != target
                and secretary not in chain
            ):
                log.tool.debug(
                    "delegate_task._run_delegation: fallback to secretary",
                    extra={"_fields": {"trace_id": trace_id, "via": secretary, "original": target}},
                )
                fb = await _attempt(secretary)
                if isinstance(fb, ToolResult):
                    return fb
                if getattr(fb, "status", None) == "ok":
                    log.tool.info(
                        "delegate_task._run_delegation: recovered via secretary",
                        extra={"_fields": {"trace_id": trace_id, "via": secretary, "original": target}},
                    )
                    # 4. EXIT — recovered path.
                    return recovered_result(t0, original=target, via=secretary, result=fb.content)
                # Fallback also failed — keep the better terminal to report.
                log.tool.warning(
                    "delegate_task._run_delegation: fallback also failed",
                    extra={"_fields": {
                        "trace_id": trace_id, "via": secretary,
                        "fb_status": getattr(fb, "status", "?"),
                    }},
                )
                if getattr(fb, "status", None) not in self._RETRIABLE:
                    result = fb
            else:
                log.tool.debug(
                    "delegate_task._run_delegation: fallback skipped",
                    extra={"_fields": {
                        "trace_id": trace_id, "secretary": secretary,
                        "caller": caller, "target": target,
                        "reason": (
                            "caller_is_secretary" if secretary == caller
                            else "target_is_secretary" if secretary == target
                            else "secretary_in_chain" if secretary in chain
                            else "no_secretary"
                        ),
                    }},
                )

        # 4. EXIT — map terminal A2AResult to ToolResult.
        return self._map_terminal(result, target, t0)

    def _map_terminal(self, result: object, target: str, t0: float) -> ToolResult:
        """Map an ``A2AResult`` status to a structured ToolResult (T7 reuses this)."""
        from stackowl.owls.a2a_delegation import A2AResult  # local import avoids circular dep

        if not isinstance(result, A2AResult):
            # Unexpected — belt-and-braces: treat as error so nothing is swallowed.
            return ok_result(
                {"status": "child_error", "to_owl": target,
                 "detail": f"specialist '{target}' returned an unexpected result type", "result": ""},
                t0, note=f"{target} returned unexpected type",
            )
        if result.status == "ok":
            return ok_result(
                {"status": "ok", "to_owl": target,
                 "result": result.content + provenance_footer(target)},
                t0, note=f"{target} handled the sub-task",
            )
        if result.status == "empty":
            return ok_result(
                {"status": "empty", "to_owl": target, "result": ""},
                t0, note=f"{target} produced no result",
            )
        if result.status == "truncated":
            return truncated_result(
                t0, target=target, result=result.content, detail=result.child_detail,
            )
        # timeout / child_error / refused
        return child_error_result(t0, target=target, detail=result.child_detail or result.status)

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

    def _charge_attempt(self, trace_id: str) -> bool:
        """Increment the per-trace cumulative attempt counter; return False past budget.

        Mirrors ``_try_acquire`` structure (same lock, same pattern). Bounded to 256
        entries to prevent an unbounded memory leak across turns/traces.
        """
        with self._lock:
            if len(self._attempts) > 256:
                self._attempts.clear()
            current = self._attempts.get(trace_id, 0)
            if current >= MAX_DELEGATION_ATTEMPTS_PER_TURN:
                return False
            self._attempts[trace_id] = current + 1
            return True
