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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

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
from stackowl.pipeline.durable.context import get_active
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import TaskStatus
from stackowl.pipeline.persistence import _structurally_irrelevant, judge_relevance
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.resolver import resolve_target
from stackowl.tools.agents.results import (
    child_error_result,
    compose_sub_task,
    cycle_result,
    error_result,
    honest_irrelevant_result,
    honest_offtopic_write_result,
    honest_uncertain_result,
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


@dataclass(frozen=True)
class _DurableChildScope:
    """The resolved durable scope for one delegation, or all-None when fail-open."""

    child_task_id: str | None = None
    durable_owner_id: str | None = None
    parent_task_id: str | None = None
    delegate_key: str | None = None


async def _resolve_durable_child_scope(
    *, caller: str, args_dict: dict[str, object],
) -> _DurableChildScope:
    """Compute the durable child scope, or a no-op scope (fail-open) (D1 §8).

    Durable ONLY when ALL hold: parent task_id present on TraceContext, an active
    DurableReActContext (for ctx.iteration), and a db_pool. Identity-determining
    values are computed explicitly from the parent task_id + ledger coordinate —
    never inferred from ambient mutable state. Any store error fails OPEN (logged)
    to the non-durable path for THIS delegation.
    """
    tctx = TraceContext.get()
    parent_task_id = tctx.get("task_id")
    durable_owner = TraceContext.durable_owner_id()
    rctx = get_active()
    db = get_services().db_pool
    if parent_task_id is None or rctx is None or db is None:
        log.tool.debug(
            "delegate_task: non-durable parent — fail-open to today's path",
            extra={"_fields": {
                "has_parent_task": parent_task_id is not None,
                "has_react_ctx": rctx is not None, "has_db": db is not None,
            }},
        )
        return _DurableChildScope()
    # Single-user invariant: no runtime path mints a non-default principal, so
    # durable_owner is None here and this resolves to DEFAULT_PRINCIPAL_ID — the
    # same owner the parent's durable rows carry. Deliberate single-user scoping,
    # not an unscoped multi-tenant gap (see DEFAULT_PRINCIPAL_ID in tenancy.principal).
    owner = durable_owner or DEFAULT_PRINCIPAL_ID
    try:
        delegate_key = idempotency_key(
            str(parent_task_id), int(rctx.iteration), "delegate_task", args_dict,
        )
        child_task_id = derive_child_task_id(delegate_key)
        store = DurableTaskStore(db, owner)
        await store.create_child_task(
            child_task_id=child_task_id, parent_task_id=str(parent_task_id),
            parent_owl=caller, delegate_key=delegate_key,
            goal=str(args_dict.get("goal", "")), owl_name=caller, channel="internal",
        )
        claimed = await store.claim_child_lease(child_task_id, lease_owner=str(parent_task_id))
        log.tool.info(
            "delegate_task: durable child scope resolved",
            extra={"_fields": {
                "parent_task_id": parent_task_id, "child_task_id": child_task_id,
                "lease_won": claimed,
            }},
        )
        return _DurableChildScope(
            child_task_id=child_task_id, durable_owner_id=owner,
            parent_task_id=str(parent_task_id), delegate_key=delegate_key,
        )
    except Exception as exc:  # B5 — fail-open: durability is additive, never breaks delegation.
        log.tool.error(
            "delegate_task: durable child setup failed — fail-open to non-durable path",
            exc_info=exc,
            extra={"_fields": {"parent_task_id": parent_task_id}},
        )
        return _DurableChildScope()


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


CommitCouplingAnswer = Literal["done", "safe_retry", "honest_uncertain"]


def resolve_commit_coupling_answer(
    *,
    child_started: bool,
    has_uncertain_effect: bool,
    has_uncommitted_intent: bool,
    child_terminal: bool,
) -> CommitCouplingAnswer:
    """The §6.2 honesty table as a pure decision (D1 §6.2).

    * never started (no intent rows)                 → "safe_retry" (pure profit).
    * an unconfirmed effect lacking a witnessed commit → "honest_uncertain".
    * any non-transactional intent not yet committed   → "honest_uncertain".
    * terminal AND every effect transactional/keyed    → "done".
    * otherwise (in-flight, no uncertainty resolvable)  → "honest_uncertain".
    """
    if not child_started:
        return "safe_retry"
    if has_uncertain_effect or has_uncommitted_intent:
        return "honest_uncertain"
    if child_terminal:
        return "done"
    return "honest_uncertain"


async def _relevance_gate(
    res: A2AResult,
    to_owl: str,
    sub_task: str,
    fast_provider: object,
) -> A2AResult:
    """Two-stage relevance gate: structural pre-filter (always) → LLM judge (if substantive + provider).

    Off-topic → demote to status="off_topic" via model_copy.  Fail-open: if
    ``fast_provider`` is None the LLM stage is skipped and the result is returned
    unchanged.  The structural stage always runs regardless of provider availability.
    References the module-level ``judge_relevance`` symbol so monkeypatching via
    ``dt.judge_relevance`` works in tests.
    """
    if _structurally_irrelevant(res.content):
        log.tool.info(
            "delegate: ok demoted by structural pre-filter",
            extra={"_fields": {"owl": to_owl}},
        )
        return res.model_copy(update={"status": "off_topic", "child_detail": "structural"})
    if fast_provider is None:
        return res
    relevant, reason = await judge_relevance(fast_provider, sub_task, res.content)  # type: ignore[arg-type]
    if not relevant:
        log.tool.warning(
            "delegate: ok judged off-topic -> demote",
            extra={"_fields": {"owl": to_owl, "reason": reason[:120]}},
        )
        return res.model_copy(update={"status": "off_topic", "child_detail": reason[:200]})
    return res


class DelegateTaskArgs(BaseModel):
    """Validated arguments for one ``delegate_task`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    to_owl: str | None = None
    role: str | None = None
    context: str | None = None


class DelegateTaskTool(Tool):
    """Delegate a focused sub-task to a specialist owl and return its result."""

    # Defense-in-depth cap on the attempt-counter dict size. Live in-flight turns
    # are NEVER evicted (their budget must not reset); only the oldest IDLE entry
    # is dropped past this bound. The natural lifecycle (evict-on-release) keeps
    # the dict far below this in normal operation (F158).
    _ATTEMPTS_MAX_ENTRIES: int = 256

    def __init__(self) -> None:
        """Construct the singleton tool.

        ``_active`` is a process-lifetime per-``trace_id`` in-flight counter for
        the width cap, guarded by a lock because concurrent ``delegate_task`` calls
        in the same turn (fan-out) mutate it from different tasks.

        ``_attempts`` is a cumulative per-``trace_id`` counter for the global
        per-turn attempt budget (``MAX_DELEGATION_ATTEMPTS_PER_TURN``). It bounds
        all delegate() calls (initial + retries + fallbacks) within one turn so a
        crafted prompt cannot walk an unbounded delegation tree.

        Lifecycle (F158): a trace's attempt counter is evicted on TURN COMPLETION
        — when its in-flight count returns to zero in ``_release`` — so the dict
        tracks only live + recently-active turns, never growing unbounded. As a
        defense-in-depth backstop a bounded LRU caps the dict at
        ``_ATTEMPTS_MAX_ENTRIES`` by evicting the OLDEST IDLE entry (never a live
        in-flight turn, whose budget would otherwise reset under it). ``_attempts``
        is insertion-ordered (a plain dict preserves order) so "oldest" is cheap.
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
            commit_coupling="unconfirmed",
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

        # 3. STEP — resolve the durable child scope (fail-open), then delegate;
        # always release the width slot.
        durable_scope = await _resolve_durable_child_scope(
            caller=caller, args_dict=args.model_dump(),
        )
        try:
            return await self._run_delegation(
                delegator=delegator, args=args, caller=caller, target=target, depth=depth,
                trace_id=trace_id, session_id=str(ctx.get("session_id") or ""),
                channel=str(ctx.get("channel") or "internal"), t0=t0,
                durable_scope=durable_scope,
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
        durable_scope: _DurableChildScope,
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
        # D3 — resolve the fast provider ONCE per ladder; fail-open if roster is dead.
        fast_provider: object = None
        try:
            fast_provider = get_services().provider_registry.get_with_cascade("fast")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — fail-open is intentional: provider/registry errors
            # (incl. an unwired None registry) must degrade to structural-only relevance, never crash
            # the delegation ladder. Drops the misleading redundant `(AllProvidersUnavailableError, Exception)`.
            log.tool.warning(
                "delegate: no fast provider for relevance judge — structural pre-filter only",
                exc_info=exc,
                extra={"_fields": {}},
            )
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
            # D1 §8.3 — when durable, the child runs under ITS OWN child_task_id so
            # the execute step assembles a durable session for it; it must NOT
            # inherit the parent's task_id. None on the non-durable / fail-open path.
            task_id=durable_scope.child_task_id,
            durable_owner_id=durable_scope.durable_owner_id,
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
            # Type guard — a misbehaving delegator returning a non-A2AResult (e.g. a bare
            # string) must never cause _attempt to raise AttributeError on res.status.
            # Coerce to an honest child_error so the ladder can handle it structurally.
            if not isinstance(res, _A2AResult):
                log.tool.error(
                    "delegate: delegator returned non-A2AResult — coercing to child_error",
                    exc_info=None,
                    extra={"_fields": {"owl": to_owl, "type": type(res).__name__}},
                )
                res = _A2AResult(status="child_error", resolved_owl=to_owl,
                                 child_detail="non-A2AResult return")
            # D3 — relevance gate: structural pre-filter → LLM judge → demote if off-topic.
            if res.status == "ok":
                res = await _relevance_gate(res, to_owl, sub_task, fast_provider)
            memo[key] = res  # D2: store result; future same-key ok hits will use this.
            return res

        async def _resolve() -> tuple[ToolResult, bool]:
            """Run the recovery ladder; return ``(ToolResult, terminal_ok)``.

            ``terminal_ok`` is the CLEAN success signal threaded out of the
            ladder (D1 §7.2): True iff the resolution produced a real answer
            (a D3-passed ``ok`` terminal or a recovered-via-secretary answer),
            False for every honest/hard terminal. The parent stamps the durable
            child ``completed``/``failed`` from this flag — never by parsing
            ``ToolResult.output``.
            """
            # 3. STEP — initial attempt.
            result = await _attempt(target)
            # If _attempt() caught an exception it already returned a ToolResult.
            if isinstance(result, ToolResult):
                return result, False

            log.tool.debug(
                "delegate_task._run_delegation: initial attempt done",
                extra={"_fields": {"trace_id": trace_id, "status": getattr(result, "status", "?")}},
            )

            # ---- UNIFIED RE-DELEGATION CAPABILITY GATE (D2) -------------------------
            # Invariant: ONLY a READ-ONLY child is ever re-delegated (retry or fallback).
            # A write-capable/unverifiable child that FAILED (retriable) or returned an
            # off-topic ok (demoted) may have ALREADY ACTED → an HONEST TERMINAL, never a
            # re-delegation (no double side-effect, no false success).

            # (1) D3-passed success → immediate terminal.
            if result.status == "ok":
                return self._map_terminal(result, target, t0), True

            redelegatable = result.status == "off_topic" or result.status in self._RETRIABLE
            if not redelegatable:
                # refused / cycle / target_not_found / truncated → terminal as-is.
                return self._map_terminal(result, target, t0), False

            # (2) Capability gate: a write-capable target may have already acted → HALT.
            #
            # DURABLE parent (D1 §6.2): replace the Story-D _can_side_effect-only
            # gate with a per-effect resolution over the child's durable record +
            # the commit_coupling of the effects it ledgered. A never-started child
            # is a DEFINITE safe-retry (pure profit); a terminal child whose every
            # effect is transactional/idempotent_keyed is DEFINITE done; anything
            # in-flight or carrying an unconfirmed/uncommitted effect stays honest.
            if durable_scope.child_task_id is not None:
                started, has_uncertain, has_uncommitted_intent, child_terminal = (
                    await self._child_ledger_facts(durable_scope)
                )
                answer = resolve_commit_coupling_answer(
                    child_started=started,
                    has_uncertain_effect=has_uncertain,
                    has_uncommitted_intent=has_uncommitted_intent,
                    child_terminal=child_terminal,
                )
                log.tool.info(
                    "delegate_task._run_delegation: commit_coupling resolution",
                    extra={"_fields": {
                        "trace_id": trace_id, "target": target, "status": result.status,
                        "answer": answer, "child_started": started,
                        "child_terminal": child_terminal,
                        "has_uncertain": has_uncertain,
                        "has_uncommitted_intent": has_uncommitted_intent,
                    }},
                )
                if answer == "done":
                    # Reuse the child's persisted answer. If the live result timed
                    # out / went empty, return the durable child's recorded result.
                    return await self._map_terminal_or_persisted(
                        result, target, t0, durable_scope,
                    ), True
                if answer == "honest_uncertain":
                    if result.status == "off_topic":
                        return honest_offtopic_write_result(target, t0), False
                    return honest_uncertain_result(target, t0), False
                # answer == "safe_retry" → fall through to the read-only ladder below.
            elif _can_side_effect(target):
                # NON-durable parent (Story-D path, unchanged): a write-capable
                # target may have already acted → HALT.
                log.tool.warning(
                    "delegate_task._run_delegation: write-capable child not re-delegated (may have acted)",
                    extra={"_fields": {"trace_id": trace_id, "target": target, "status": result.status}},
                )
                if result.status == "off_topic":
                    return honest_offtopic_write_result(target, t0), False
                return honest_uncertain_result(target, t0), False

            # ---- read-only target → safe to re-delegate -----------------------------
            # D1 §9 — the parent is ABANDONING this timed-out/off-topic child and
            # advancing a ladder rung (retry same owl, or fallback to secretary).
            # Stamp the child superseded so a slow eventual commit is neutralized at
            # the decision layer (defensive). Reached ONLY here: the "ok"/"done"
            # (answer reused) and "honest_uncertain" (child halted) terminals all
            # returned above, so this never fires on a done/reuse path. No-op +
            # fail-open on the non-durable path.
            await self._supersede_durable_child(durable_scope)

            # (3) Transport failure → ONE same-owl retry. off_topic SKIPS the retry
            # (it is not a transport failure) and proceeds straight to fallback.
            if result.status in self._RETRIABLE:
                log.tool.debug(
                    "delegate_task._run_delegation: read-only retry-once",
                    extra={"_fields": {"trace_id": trace_id, "target": target, "prev_status": result.status}},
                )
                result = await _attempt(target)
                if isinstance(result, ToolResult):
                    return result, False
                if result.status == "ok":
                    return self._map_terminal(result, target, t0), True
                if not (result.status == "off_topic" or result.status in self._RETRIABLE):
                    # Retry produced a hard terminal (refused/etc.) → report it as-is.
                    return self._map_terminal(result, target, t0), False

            # off_topic OR transport-retry-exhausted → fallback to a DIFFERENT owl.
            registry = get_services().owl_registry
            secretary = registry.secretary_name() if registry is not None else None
            # Skip self-fallback and in-chain fallback (preserves the no-escalation rule).
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
                    return fb, False
                if fb.status == "ok":
                    log.tool.info(
                        "delegate_task._run_delegation: recovered via secretary",
                        extra={"_fields": {"trace_id": trace_id, "via": secretary, "original": target}},
                    )
                    # 4. EXIT — recovered path.
                    return recovered_result(t0, original=target, via=secretary, result=fb.content), True
                # Fallback also failed (off-topic / retriable / hard) → honest irrelevant.
                log.tool.warning(
                    "delegate_task._run_delegation: fallback also failed — honest irrelevant",
                    extra={"_fields": {
                        "trace_id": trace_id, "via": secretary,
                        "fb_status": getattr(fb, "status", "?"),
                    }},
                )
                return honest_irrelevant_result(t0), False

            log.tool.debug(
                "delegate_task._run_delegation: fallback skipped — honest irrelevant",
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
            # 4. EXIT — no eligible fallback owl → honest irrelevant terminal.
            return honest_irrelevant_result(t0), False

        final, terminal_ok = await _resolve()
        # Single tail — terminalize the durable child as a PROJECTION of this
        # resolution (D1 §7.2): "completed" iff the ladder produced a real answer
        # (terminal_ok), "failed" otherwise. No-op on the non-durable path.
        await self._terminalize_durable_child(durable_scope, completed=terminal_ok)
        return final

    async def _terminalize_durable_child(
        self, durable_scope: _DurableChildScope, *, completed: bool,
    ) -> None:
        """Stamp the durable child terminal as a projection of this resolution (D1 §7.2).

        No-op on the fail-open / non-durable path (child_task_id is None). Fail-open
        on store error (logged) — terminalization is belt-and-suspenders to the
        reaper, never a reason to fail the parent's turn.
        """
        if durable_scope.child_task_id is None:
            return
        status: TaskStatus = "completed" if completed else "failed"
        try:
            db = get_services().db_pool
            if db is None:  # pragma: no cover — durable scope implies a db, defensive
                return
            store = DurableTaskStore(db, durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID)
            await store.terminalize_child(durable_scope.child_task_id, status)
            log.tool.info(
                "delegate_task: terminalized durable child",
                extra={"_fields": {
                    "child_task_id": durable_scope.child_task_id, "status": status,
                }},
            )
        except Exception as exc:  # B5 — reaper is the backstop; never fail the turn.
            log.tool.error(
                "delegate_task: terminalize child failed — reaper will reconcile",
                exc_info=exc,
                extra={"_fields": {"child_task_id": durable_scope.child_task_id}},
            )

    async def _supersede_durable_child(self, durable_scope: _DurableChildScope) -> None:
        """Tombstone the durable child when the parent advances past it (D1 §9).

        Called ONLY where the parent abandons a timed-out/off-topic child and
        advances a ladder rung (retry / fallback) — never on a done or
        answer-reused terminal. No-op on the non-durable path (child_task_id is
        None); fail-open on store error (logged, never crashes the parent).
        """
        if durable_scope.child_task_id is None:
            return
        try:
            db = get_services().db_pool
            if db is None:  # pragma: no cover — durable scope implies a db, defensive
                return
            store = DurableTaskStore(
                db, durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID,
            )
            await store.supersede_child(durable_scope.child_task_id)
            log.tool.info(
                "delegate_task: superseded durable child (ladder advanced)",
                extra={"_fields": {"child_task_id": durable_scope.child_task_id}},
            )
        except Exception as exc:  # B5 — supersession is defensive; never fail the turn.
            log.tool.error(
                "delegate_task: supersede child failed",
                exc_info=exc,
                extra={"_fields": {"child_task_id": durable_scope.child_task_id}},
            )

    async def _child_ledger_facts(
        self, durable_scope: _DurableChildScope,
    ) -> tuple[bool, bool, bool, bool]:
        """Return ``(child_started, has_uncertain_effect, has_uncommitted_intent,
        child_terminal)`` for the durable child (D1 §6.2).

        Reads the child's ``side_effect_ledger`` rows (by ``owner_id`` +
        ``task_id``) and the child's ``tasks.status``, cross-referencing each
        ledgered tool against the registry's ``commit_coupling``:

        * ``started``               any ledger row exists under the child id.
        * ``child_terminal``        the durable child is ``completed``/``failed``.
        * ``has_uncommitted_intent`` an ``intent``-not-``committed`` effect whose
          coupling is NOT ``transactional``/``idempotent_keyed``.
        * ``has_uncertain_effect``   an ``unconfirmed``-coupling effect (committed
          or not) — a lossy-ack boundary where intent and effect can diverge.

        A tool whose coupling is ``None`` (undeclared, or a tool no longer in the
        registry) is treated as ``unconfirmed`` (fail-safe — never silently safe).
        Fail-open: on any store/registry error return the maximally-uncertain
        tuple ``(True, True, True, False)`` so the answer stays honest_uncertain.
        """
        db = get_services().db_pool
        owner = durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID
        cid = durable_scope.child_task_id
        treg = get_services().tool_registry
        try:
            if db is None:  # pragma: no cover — durable scope implies a db, defensive
                return True, True, True, False
            rows = await db.fetch_all(
                "SELECT tool_name, status FROM side_effect_ledger "
                "WHERE owner_id = ? AND task_id = ?",
                (owner, cid),
            )
            store = DurableTaskStore(db, owner)
            child = await store.get(str(cid))
            started = len(rows) > 0
            child_terminal = child.status in ("completed", "failed")
            has_uncertain = False
            has_uncommitted_intent = False
            for r in rows:
                coupling: str | None = None
                if treg is not None:
                    tool = treg.get(str(r["tool_name"]))
                    if tool is not None:
                        coupling = tool.manifest.commit_coupling
                safe = coupling in ("transactional", "idempotent_keyed")
                committed = str(r["status"]) == "committed"
                if not committed and not safe:
                    has_uncommitted_intent = True
                if coupling == "unconfirmed":
                    has_uncertain = True
            return started, has_uncertain, has_uncommitted_intent, child_terminal
        except Exception as exc:  # B5 — fail to maximally-uncertain (honest).
            log.tool.error(
                "delegate_task: child ledger read failed — defaulting honest_uncertain",
                exc_info=exc,
                extra={"_fields": {"child_task_id": cid}},
            )
            return True, True, True, False

    async def _map_terminal_or_persisted(
        self, result: object, target: str, t0: float, durable_scope: _DurableChildScope,
    ) -> ToolResult:
        """The "done" leg (D1 §6.2): reuse the child's answer.

        If the live ``result`` is a clean ``ok`` terminal, map it directly
        (``_map_terminal``). The "done" answer is otherwise reached on a
        retriable/off_topic live status (the ``status == "ok"`` short-circuit
        fired earlier), so the live content is unusable — instead reuse the
        durable child's persisted ``tasks.result``. Fail-safe: if the persisted
        result is unreadable/empty, fall back to the honest-uncertain terminal so
        nothing is invented.
        """
        from stackowl.owls.a2a_delegation import A2AResult  # local import avoids circular dep

        if isinstance(result, A2AResult) and result.status == "ok":
            return self._map_terminal(result, target, t0)

        persisted: str | None = None
        try:
            db = get_services().db_pool
            if db is not None:
                store = DurableTaskStore(
                    db, durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID,
                )
                child = await store.get(str(durable_scope.child_task_id))
                persisted = child.result
        except Exception as exc:  # B5 — fail-safe to honest-uncertain.
            log.tool.error(
                "delegate_task: persisted child result read failed",
                exc_info=exc,
                extra={"_fields": {"child_task_id": durable_scope.child_task_id}},
            )
            persisted = None
        if not persisted:
            return honest_uncertain_result(target, t0)
        return ok_result(
            {"status": "ok", "to_owl": target,
             "result": persisted + provenance_footer(target)},
            t0, note=f"{target} completed durably (reused persisted result)",
        )

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
        # off_topic is now routed by the unified gate in _run_delegation (honest
        # terminals) and never reaches here; the default below is a safe catch-all.
        # timeout / child_error / refused / off_topic
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
        """Decrement the per-trace in-flight counter; drop the key at zero.

        When in-flight returns to zero the turn's delegation activity is over, so
        the per-turn ATTEMPT counter is evicted too (F158) — tying its lifetime to
        the turn instead of a global size threshold. A future turn (even one that
        recycles the same trace id) then starts with a fresh budget.
        """
        with self._lock:
            current = self._active.get(trace_id, 0)
            if current <= 1:
                self._active.pop(trace_id, None)
                # Turn complete — evict its attempt budget (natural lifecycle).
                self._attempts.pop(trace_id, None)
            else:
                self._active[trace_id] = current - 1

    def _charge_attempt(self, trace_id: str) -> bool:
        """Increment the per-trace cumulative attempt counter; return False past budget.

        Mirrors ``_try_acquire`` structure (same lock, same pattern). The dict is
        bounded by the natural evict-on-release lifecycle; this method only adds a
        defense-in-depth LRU backstop that evicts the OLDEST IDLE entry — NEVER a
        live in-flight turn (F158: the old ``clear()`` nuked every live turn's
        budget). Insertion-ordered dict makes "oldest" the first key.
        """
        with self._lock:
            if len(self._attempts) >= self._ATTEMPTS_MAX_ENTRIES:
                self._evict_oldest_idle_locked()
            current = self._attempts.get(trace_id, 0)
            if current >= MAX_DELEGATION_ATTEMPTS_PER_TURN:
                return False
            self._attempts[trace_id] = current + 1
            return True

    def _evict_oldest_idle_locked(self) -> None:
        """Drop the oldest attempt-counter entry whose turn is NOT in-flight.

        Caller MUST hold ``self._lock``. A live trace (``_active`` > 0) is skipped
        so its per-turn budget is never reset mid-turn. Iterates oldest→newest
        (insertion order) and removes the first idle entry; if every entry is live
        (pathological), nothing is evicted — correctness (never reset a live rail)
        wins over the soft size bound, and the dict is still bounded by the number
        of concurrent turns.
        """
        for candidate in list(self._attempts):
            if self._active.get(candidate, 0) == 0:
                self._attempts.pop(candidate, None)
                log.tool.debug(
                    "delegate_task._charge_attempt: LRU-evicted idle attempt counter",
                    extra={"_fields": {"evicted_trace": candidate}},
                )
                return
