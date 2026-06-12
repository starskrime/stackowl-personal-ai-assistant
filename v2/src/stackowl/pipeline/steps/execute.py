"""Pipeline step 4: execute — stream from ModelProvider through OwlResourceGuard."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from stackowl.authz.bounds import ResourceCaps
from stackowl.commands.tier_command import get_session_tier
from stackowl.exceptions import (
    AllProvidersUnavailableError,
    BudgetBreach,
    DurableReplayUncertain,
    OwlConcurrencyError,
    OwlTimeoutError,
    OwlTokenLimitError,
    ProviderNotFoundError,
    TurnStopped,
)
from stackowl.infra import recovery_context
from stackowl.infra.observability import log
from stackowl.owls.guards import OwlResourceGuard
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.authz_compose import compute_effective_bounds
from stackowl.pipeline.budget import BudgetGovernor, make_budget_callback
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.providers.base import Message, ModelProvider
from stackowl.providers.react_callback import ReActIterationState
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from stackowl.gateway.turn_registry import TurnRegistry
    from stackowl.pipeline.services import StepServices
    from stackowl.providers.react_callback import IterationCallback
    from stackowl.skills.store import SkillIndexStore

# W1.T3 — the deliver-vs-giveup checker the provider loop calls just before
# accepting a final answer: (draft, tools_tried) -> corrective directive | None.
PersistenceCheck = Callable[[str, list[str]], Awaitable[str | None]]

# E8-S0 — tools a delegated child (delegation_depth>0) must NOT see, so a child
# cannot recurse into a fork-bomb. Names are matched defensively (the tools are
# registered by later stories S1/S3); excluding by name is correct ahead of them.
# E9-S2/FF-E9-5 — `process` joins them: a child handles its sub-task and returns
# without leaving persistent OS processes running past the parent turn (the S0
# count-cap + mandatory TTL still bound the top-level owl).
# E11-S5 GAP-B — `execute_code` joins them: a delegated sub-agent must NOT run
# arbitrary code in a sandbox. The top-level owl runs code under the consent gate;
# a child handles its sub-task and returns, never recursing into code execution.
_CHILD_EXCLUDED_TOOLS = frozenset(
    {"delegate_task", "sessions_spawn", "sessions_send", "process", "execute_code", "owl_build"}
)


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Most-recent assistant text in the live message list (partial work on stop)."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return str(m["content"])
    return ""


def build_persistence_check(
    state: PipelineState,
    services: StepServices,
    *,
    primary: ModelProvider | None = None,
    fallback: ModelProvider | None = None,
) -> PersistenceCheck:
    """Build the deliver-vs-giveup checker the provider loop invokes per turn.

    W1.T3 — covers ALL turns (no interactive/depth gate) and adds a fallback judge
    tier: the primary judge (``preg.get_with_cascade("fast")``) and, on its failure,
    a DIFFERENT local/always-available judge (``preg.get_with_cascade("local")``) so
    a single model erroring no longer silently accepts a give-up.

    ``primary``/``fallback`` may be injected (tests); when None they are resolved
    lazily from ``services.provider_registry`` at call time so a hot provider reload
    is picked up. Fail-OPEN: if neither judge can rule (no registry, both raise) the
    checker returns ``None`` — the answer is accepted and the turn never hangs/loops;
    the structural veto in the provider remains the backstop.
    """
    from stackowl.pipeline.persistence import (
        JUDGE_ERROR_REASON,
        PERSISTENCE_DIRECTIVE,
        judge_delivery,
    )

    async def _persistence_check(draft: str, tools_tried: list[str]) -> str | None:
        preg = services.provider_registry
        # PRIMARY judge — resolve (injected or "fast" tier) and rule. judge_delivery
        # is itself fail-OPEN: on a provider/parse error it returns
        # (True, JUDGE_ERROR_REASON) instead of raising. So a primary failure shows
        # up EITHER as a raised exception (provider lookup) OR as that sentinel —
        # both route to the fallback tier rather than silently accepting a give-up.
        try:
            judge = primary if primary is not None else (
                preg.get_with_cascade("fast") if preg is not None else None
            )
            if judge is None:  # no registry → cannot judge; accept (fail open)
                return None
            delivered, reason = await judge_delivery(
                judge, state.input_text, draft, tools_tried
            )
        except Exception as exc:  # primary provider lookup raised
            log.engine.warning(
                "[pipeline] execute: primary persistence judge raised — "
                "trying fallback judge",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            delivered, reason = True, JUDGE_ERROR_REASON

        if reason == JUDGE_ERROR_REASON:  # primary failed open — try the fallback tier
            log.engine.warning(
                "[pipeline] execute: primary persistence judge failed — "
                "trying fallback judge",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            try:
                fb = fallback if fallback is not None else (
                    preg.get_with_cascade("local") if preg is not None else None
                )
                if fb is None:  # no fallback available — accept (fail open)
                    return None
                delivered, reason = await judge_delivery(
                    fb, state.input_text, draft, tools_tried
                )
            except Exception as exc2:  # fallback lookup also raised — final fail OPEN
                log.engine.error(
                    "[pipeline] execute: fallback persistence judge also failed — "
                    "accepting answer",
                    exc_info=exc2,
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
                return None
        if not delivered:
            log.engine.info(
                "[pipeline] execute: persistence judge ruled give-up — nudging",
                extra={"_fields": {"trace_id": state.trace_id, "reason": reason[:120]}},
            )
            return PERSISTENCE_DIRECTIVE
        return None

    return _persistence_check


def make_steering_callback(
    registry: TurnRegistry | None,
    request_id: str,
) -> IterationCallback | None:
    """Build the per-turn steering-drain + cooperative-stop callback (concurrent-msg
    §5.1 Task 10, §5.3 Task 12).

    Returns an ``IterationCallback`` the provider invokes at each ReAct iteration
    boundary. It reaches THIS turn's steering mailbox via
    ``registry.get(request_id).steering_mailbox`` (request_id == state.trace_id)
    and drains it with ``get_nowait()`` in a loop — NEVER ``await get()``, which
    would block the iteration boundary forever when no steering is pending. All
    drained items are coalesced into ONE ``{"role": "user", "content": "[steering]
    ..."}`` message folded into the loop (Task 9 splice contract).

    Cooperative STOP (§5.3): the SAME boundary checks the turn's ``stop_requested``
    FLAG. When set, the callback raises ``TurnStopped`` to END the loop GRACEFULLY
    — NOT ``task.cancel()`` (a cancel raises mid-tool → torn state). The exception
    propagates out of the provider's ``complete_with_tools`` (the same path
    ``BudgetBreach`` uses — the provider awaits the callback directly, only
    ``openai.APIError`` is caught around the API call, never the callback) and is
    caught by the execute step, which finalizes with a "stopped" chunk carrying the
    partial work. Stop is honored at the iteration BOUNDARY: the in-flight tool
    batch is fully observed first (cooperative at iteration granularity). The flag
    is checked AFTER draining so a co-arriving steer is never silently swallowed by
    the stop.

    Fail-safe by construction:
      * No registry wired (``registry is None``) → returns ``None`` (NO callback at
        all), so the default provider call stays byte-for-byte unchanged (no
        ``on_iteration_complete`` kwarg) — the recon's preserved contract.
      * Registry present but no registered turn for this request_id, or an empty
        mailbox at an iteration boundary → the callback returns ``None`` (no
        steering, the loop proceeds normally).
    Raises ONLY the controlled ``TurnStopped`` (a control-flow signal), never an
    error.
    """
    if registry is None:
        return None

    async def _cb(_state: ReActIterationState) -> list[dict[str, Any]] | None:
        # 1. ENTRY / 2. DECISION — fail-safe: no turn for this request → no steering.
        turn = registry.get(request_id)
        if turn is None:
            return None
        # 3. STEP — drain to empty with get_nowait (NEVER await get(), which would
        # block the iteration boundary forever when the mailbox is empty).
        drained: list[str] = []
        while True:
            try:
                drained.append(turn.steering_mailbox.get_nowait())
            except asyncio.QueueEmpty:
                break
        # §5.3 — honor cooperative STOP at this iteration boundary. Checked AFTER
        # draining so we observe (and count via `pending_steers`) any co-arriving
        # steer before we stop. Those drained steers are then DISCARDED — the turn
        # is stopping, so there is no further iteration to fold them into; this is
        # intentional, not a lost-steer bug. FLAG only — we raise a controlled
        # TurnStopped, NEVER task.cancel().
        if turn.stop_requested:
            log.engine.info(
                "[steer] stop flag honored at iteration boundary — finalizing gracefully",
                extra={"_fields": {
                    "request_id": request_id,
                    "iteration": _state.iteration,
                    "pending_steers": len(drained),
                }},
            )
            from stackowl.exceptions import TurnStopped

            raise TurnStopped(
                request_id,
                partial_text=_last_assistant_text(_state.messages),
                tool_call_records=list(_state.tool_call_records),
            )
        if not drained:
            return None
        # 4. EXIT — coalesce all drained items into one [steering] user message.
        merged = " ".join(drained)
        log.engine.debug(
            "[steer] folding steering messages",
            extra={"_fields": {"request_id": request_id, "count": len(drained)}},
        )
        return [{"role": "user", "content": f"[steering] {merged}"}]

    return _cb


async def _compute_presented_pins(
    base_pins: list[str],
    owned_skill_names: tuple[str, ...],
    skill_store: SkillIndexStore | None,
) -> list[str]:
    """presented_pins = base owl tools ∪ owned skills' tool names. PRESENTATION ONLY —
    the dispatch seam enforces owl.bounds ∩ creation_ceiling independently (see
    compute_effective_bounds), so a coupled tool is visible but still DENIED unless
    bounds permit. Never an authorization widening."""
    # 1. ENTRY
    log.engine.debug(
        "[pipeline] execute: _compute_presented_pins: entry",
        extra={"_fields": {"base_pins": len(base_pins), "owned_skills": list(owned_skill_names)}},
    )
    pins = list(base_pins)
    if owned_skill_names and skill_store is not None:
        try:
            # 2. DECISION — fetch owned skills and union their tool_names
            skills = await skill_store.get_many_by_name(tuple(owned_skill_names))
            log.engine.debug(
                "[pipeline] execute: _compute_presented_pins: fetched skills",
                extra={"_fields": {"fetched": len(skills)}},
            )
            # 3. STEP — merge tool names, deduplicating
            for sk in skills:
                for tn in sk.tool_names:
                    if tn not in pins:
                        pins.append(tn)
        except Exception as exc:  # B5 — coupling is best-effort, never break the turn
            log.engine.warning(
                "[pipeline] execute: skill pin augmentation failed",
                exc_info=exc,
                extra={"_fields": {"owl_skills": list(owned_skill_names)}},
            )
    # 4. EXIT
    log.engine.debug(
        "[pipeline] execute: _compute_presented_pins: exit",
        extra={"_fields": {"total_pins": len(pins)}},
    )
    return pins


def _schema_tool_name(schema: dict[str, object]) -> str:
    """Extract the tool name from a provider schema (anthropic or openai shape)."""
    name = schema.get("name")
    if isinstance(name, str):  # anthropic protocol shape
        return name
    fn = schema.get("function")
    if isinstance(fn, dict):  # openai protocol shape: {"function": {"name": ...}}
        inner = fn.get("name")
        if isinstance(inner, str):
            return inner
    return ""


def _exclude_spawn_tools(schemas: list[dict[str, object]]) -> list[dict[str, object]]:
    """Drop spawn/delegate tools from a presented schema list (depth>0 children)."""
    return [s for s in schemas if _schema_tool_name(s) not in _CHILD_EXCLUDED_TOOLS]


async def _try_substitute(
    *,
    failed_tool: str,
    failed_args: dict[str, object],
    tool_registry: ToolRegistry,
    effective: Any,
    substituted_tags: set[str],
    trace_id: str,
) -> str | None:
    """The W3.T14 recovery actuator (route around a broken capability).

    Find an in-bounds, NON-consequential sibling sharing the failed tool's
    capability_tag, run it through the SAME guarded path (ledger_guard), and on
    success return its output as a fresh observation prefixed with a neutral
    localized note. Returns ``None`` (caller falls through to TOOL_FAILED) when:
    no sibling is eligible, the sibling itself fails, or ANY actuator error
    occurs. NEVER raises — the substitution must never crash the turn.

    CONSENT-SAFETY: ``find_substitute`` only ever returns read/write siblings, so
    a consequential tool is never auto-run here (no consent bypass). BOUNDS-SAFETY:
    the ``in_bounds`` callable reuses the SAME ``check_effective_bounds`` verdict.
    """
    from stackowl.authz.bounds_guard import check_effective_bounds
    from stackowl.pipeline.capability_substitution import find_substitute
    from stackowl.pipeline.durable.ledger_guard import ledger_guard
    from stackowl.setup.localize import localize_format

    try:
        match = find_substitute(
            failed_tool,
            failed_args,
            registry=tool_registry,
            in_bounds=lambda n: check_effective_bounds(effective, n) is None,
            already_substituted=substituted_tags,
        )
        if match is None:
            return None
        sibling_name, sibling_args = match
        sib = tool_registry.get(sibling_name)
        if sib is None:  # raced/unregistered — degrade honestly
            log.engine.warning(
                "[pipeline] execute: substitute sibling vanished from registry",
                extra={"_fields": {"sibling": sibling_name, "trace_id": trace_id}},
            )
            return None
        log.engine.info(
            "[pipeline] execute: self-heal substitution — routing around failed tool",
            extra={"_fields": {
                "failed_tool": failed_tool, "sibling": sibling_name, "trace_id": trace_id,
            }},
        )
        # Run the sibling through the SAME guarded path the primary used. The
        # sibling is read/write (find_substitute guarantees it), so under a durable
        # context ledger_guard treats a read as passthrough and a write as
        # exactly-once — identical to a direct dispatch of that tool.
        sib_result = await ledger_guard(
            sibling_name, sibling_args, sib.manifest.action_severity,
            lambda: sib(**sibling_args),
        )
        if not sib_result.success:
            log.engine.info(
                "[pipeline] execute: substitute sibling also failed — falling through",
                extra={"_fields": {
                    "failed_tool": failed_tool, "sibling": sibling_name, "trace_id": trace_id,
                }},
            )
            return None
        # SUCCESS — record the capability so this turn does not substitute again
        # for the same class, and return the sibling's output as a fresh
        # observation with a neutral localized note so the model knows an
        # alternative produced it.
        # Record the recovery so the user can be told (machinery-recorded, true by
        # construction) and the turn's recovery log captures it.
        recovery_context.record_recovery(
            kind="substitution", failed=failed_tool,
            recovered_via=sibling_name, user_visible=True,
        )
        tag = sib.manifest.capability_tag
        if tag:
            substituted_tags.add(tag)
        note = localize_format(
            "self_heal_substituted", "en", failed=failed_tool, sibling=sibling_name,
        )
        log.engine.info(
            "[pipeline] execute: self-heal substitution succeeded",
            extra={"_fields": {
                "failed_tool": failed_tool, "sibling": sibling_name,
                "tag": tag, "trace_id": trace_id,
            }},
        )
        return f"{note}\n{sib_result.output}"
    except Exception as exc:  # noqa: BLE001 — the actuator must never crash the turn
        log.engine.error(
            "[pipeline] execute: self-heal substitution actuator failed — falling through",
            exc_info=exc,
            extra={"_fields": {"failed_tool": failed_tool, "trace_id": trace_id}},
        )
        return None


async def _run_with_tools(
    state: PipelineState,
    provider: ModelProvider,
    tool_registry: ToolRegistry,
) -> PipelineState:
    """Execute the provider's tool loop and return updated state."""
    # E1-S4 — DNA-gated presented set: an owl with a non-empty capability_profile
    # sees base ∪ its groups ∪ pins ∪ tool_search (capped); overflow via tool_search.
    # Owls without a profile keep the full catalog (no regression).
    #
    # NOTE: gating is PRESENTATION, not authorization. _dispatch (below) resolves
    # tools from the FULL registry, so a tool_search'd overflow tool stays callable
    # by name even when it is not in this turn's schema — that is how overflow stays
    # reachable. The consent gate (not gating) is the real access-control boundary.
    profile: list[str] | None = None
    pins: list[str] | None = None
    # E1-S4 — capability_profile gating: a per-owl presented tool set (base ∪ groups
    # ∪ pins ∪ tool_search). The BOUNDS check (effective = owl ∩ ceiling ∩ envelope)
    # is now in _dispatch via compute_effective_bounds (E2-S2); this block is ONLY for
    # DNA-gated presentation (a different, weaker control — presentation, not authz).
    owl_registry = get_services().owl_registry
    if owl_registry is not None:
        try:
            owl_manifest = owl_registry.get(state.owl_name)
            if owl_manifest.capability_profile:
                profile = list(owl_manifest.capability_profile)
                # Presentation-only: presented_pins = base tools ∪ owned-skill tool names.
                # Enforcement (owl.bounds ∩ creation_ceiling) is independent in _dispatch.
                pins = await _compute_presented_pins(
                    owl_manifest.tools, owl_manifest.skills, get_services().skill_store
                )
        except Exception as exc:  # unknown owl / lookup failure → no gating (safe)
            log.engine.debug(
                "[pipeline] execute: owl profile lookup failed — full catalog",
                exc_info=exc, extra={"_fields": {"owl": state.owl_name}},
            )
    # E2-S3 — least-privilege presentation: when the task has a planned
    # envelope, restrict the presented set to plan ∪ discovery (drift
    # prevention). None envelope → restrict_to=None → byte-for-byte S2.
    restrict_to = state.task_envelope.tools if state.task_envelope is not None else None
    tool_schemas = tool_registry.to_provider_schema(
        provider.protocol, profile=profile, pins=pins, restrict_to=restrict_to
    )
    # E8-S0 — child-toolset exclusion (PRIMARY fork-bomb cap): a delegated child
    # (delegation_depth>0) may not itself spawn/delegate, so remove those two
    # tools from the PRESENTED set. Excluded by NAME defensively so it is correct
    # once S1/S3 register delegate_task/sessions_spawn (they don't exist yet).
    if state.delegation_depth > 0:
        tool_schemas = _exclude_spawn_tools(tool_schemas)
        log.engine.debug(
            "[pipeline] execute: depth>0 — excluding spawn/delegate tools",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "delegation_depth": state.delegation_depth,
                "tools": len(tool_schemas),
            }},
        )
    log.engine.info(
        "[pipeline] execute: tool_loop entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name, "tools": len(tool_schemas)}},
    )

    # F3.1 — within a single run, a tool denied once must not re-prompt the user
    # if the model stubbornly re-calls it; short-circuit subsequent calls.
    denied_this_run: set[str] = set()
    # E2-S3 — off-plan tools already drift-logged this run (de-duplicate per tool).
    drift_audited: set[str] = set()
    # W3.T14 — capability_tags already auto-substituted this turn (one route-around
    # per capability per turn; a second failure of the same class falls through to
    # the honest TOOL_FAILED marker rather than substituting again).
    substituted_tags: set[str] = set()

    async def _dispatch(name: str, args: dict[str, object]) -> str:
        # F3.1 / E2-S1 loop-stop — a tool already denied this run (by consent OR by
        # bounds) short-circuits HERE before any re-check, so a model that
        # stubbornly re-calls a refused tool gets a stable "already declined"
        # signal instead of a fresh full check every iteration (no loop).
        if name in denied_this_run:
            log.engine.info(
                "[pipeline] execute: tool already declined this run — not re-prompting",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
            )
            return (
                f"The action '{name}' was already declined this turn. Do not call it again — "
                "respond to the user instead."
            )
        # E8-S0 — EXECUTION-layer fork-bomb cap (not just presentation). A delegated
        # child (delegation_depth>0) is refused these tools even if it names one the
        # presented schema omitted: presentation gating is not authorization, so the
        # depth gate must enforce HERE, fail-closed, from the TRUSTED state.
        if state.delegation_depth > 0 and name in _CHILD_EXCLUDED_TOOLS:
            log.engine.warning(
                "[pipeline] execute: depth>0 child denied spawn/delegate tool",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                   "delegation_depth": state.delegation_depth}},
            )
            return (
                f"'{name}' is not available to a delegated sub-agent (delegation depth "
                f"limit reached). Complete the task yourself and return your result."
            )
        # E2-S2 (FR33/FR35-adjacent) — BOUNDS check against EFFECTIVE bounds:
        # owl.bounds(now) ∩ state.creation_ceiling (enforcement). Checked
        # before consent/execution. Fail-closed: a bounded-owl computation error
        # DENIES (never falls through on a security path); an unbounded owl yields
        # None → unchanged (byte-for-byte S1). task_envelope is NOT enforced here
        # (E2-S3: it drives presentation + drift telemetry only).
        from stackowl.authz.bounds_guard import check_effective_bounds
        from stackowl.pipeline.authz_compose import compute_effective_bounds

        try:
            effective = compute_effective_bounds(state, get_services().owl_registry)
        except Exception as exc:
            denied_this_run.add(name)
            log.engine.error(
                "[pipeline] execute: bounds computation failed — denying (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": name, "owl": state.owl_name, "trace_id": state.trace_id}},
            )
            return (
                f"The action '{name}' could not be authorized (bounds check failed) and "
                "was not run. Respond to the user instead."
            )
        bounds_block = check_effective_bounds(effective, name)
        if bounds_block is not None:
            denied_this_run.add(name)
            # Provenance for the log only (deny branch only — no per-dispatch
            # recompute on the allow path). Guarded: a transient fault while
            # recomputing the owl-only verdict must NOT abort the turn — the tool
            # is already denied; fall back to "unknown" provenance and return the
            # clean deny.
            try:
                owl_only = check_effective_bounds(
                    compute_effective_bounds(
                        state.evolve(creation_ceiling=None, task_envelope=None),
                        get_services().owl_registry,
                    ),
                    name,
                )
                denied_by = "owl" if owl_only is not None else "task"
            except Exception:  # noqa: BLE001 — provenance is best-effort, never fatal
                denied_by = "unknown"
            log.engine.warning(
                "[pipeline] execute: tool refused by bounds",
                extra={"_fields": {
                    "tool": name, "owl": state.owl_name, "trace_id": state.trace_id,
                    "axis": "tools", "denied_by": denied_by,
                }},
            )
            return bounds_block
        # E2-S3 — drift telemetry (OBSERVE-ONLY). A durable task carries a
        # least-privilege task_envelope; a tool outside it still runs (the hard
        # boundary owl∩ceiling already permitted it) but is logged once as drift.
        # Honest-case telemetry, NOT adversarial detection. Never blocks.
        te = state.task_envelope
        if te is not None and te.tools is not None and name not in te.tools and name not in drift_audited:
            drift_audited.add(name)
            log.engine.warning(
                "[authz] drift: off-plan tool used",
                extra={"_fields": {"tool": name, "owl": state.owl_name, "trace_id": state.trace_id}},
            )
        t = tool_registry.get(name)
        if t is None:
            log.engine.warning("[pipeline] execute: unknown tool in dispatch", extra={"_fields": {"tool": name}})
            return f"Tool not found: {name}"
        # E0-S1 — consent gate runs BEFORE execution for consequential tools.
        # The category is derived inside gate.check() from the TRUSTED manifest,
        # never from LLM-supplied args. Fail closed: a gate error, OR a missing
        # gate on a consequential tool, denies rather than runs it.
        gate = get_services().consent_gate
        is_consequential = t.manifest.action_severity == "consequential"
        if gate is not None:
            try:
                # E11-S5 GAP-A — pass the validated call args so a tool's
                # consent_summary() can show WHAT will run (e.g. execute_code's
                # code + language + network), not just the static description.
                allowed = await gate.check(
                    t, channel=state.channel, session_id=state.session_id, call_args=args
                )
            except Exception as exc:
                log.engine.error(
                    "[pipeline] execute: consent gate raised — denying (fail closed)",
                    exc_info=exc,
                    extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
                )
                allowed = False
        elif is_consequential:
            # No gate wired but the tool is consequential → fail closed (never run
            # a consequential action without a functioning consent control).
            log.engine.error(
                "[pipeline] execute: consequential tool but NO consent gate wired — denying",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
            )
            allowed = False
        else:
            allowed = True
        if not allowed:
            denied_this_run.add(name)
            log.engine.info(
                "[pipeline] execute: consequential action declined by gate",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id, "session_id": state.session_id}},
            )
            return (
                f"The action '{name}' requires your approval and was not run because consent "
                "was declined or not granted. Ask the user to approve it if they want it to proceed."
            )
        # S2 durable-react — route the real tool call through the exactly-once
        # ledger guard. DORMANT: with no active DurableReActContext (every path
        # today) this is a transparent `await t(**args)`. Only a side-effecting
        # tool under an active durable task is ledger-guarded (exactly-once).
        from stackowl.pipeline.durable.ledger_guard import ledger_guard

        tr = await ledger_guard(name, args, t.manifest.action_severity, lambda: t(**args))
        # Learning Commit 5 — post-execute heuristic match + event emission.
        # Zero behavior change; downstream subscribers (classify, future hooks)
        # see "tool.heuristic_match" when a known-bad pattern fires.
        services = get_services()
        if services.heuristic_store is not None and services.event_bus is not None:
            from stackowl.learning.heuristic_matcher import match_and_emit

            try:
                await match_and_emit(
                    tool_name=name, tool_result=tr,
                    heuristic_store=services.heuristic_store,
                    event_bus=services.event_bus,
                )
            except Exception as exc:  # B5 — never block dispatch on a telemetry hook
                log.engine.warning(
                    "[pipeline] execute: heuristic match failed — continuing",
                    exc_info=exc,
                    extra={"_fields": {"tool": name}},
                )
        if tr.success:
            return tr.output
        # FAILED — W3.T14 recovery actuator: before surrendering to the marker,
        # deterministically route around the broken capability. If this tool
        # declares a capability_tag, look for an in-bounds, NON-consequential
        # sibling that produces the same KIND of result and run IT through the
        # SAME guarded path, feeding its success back as a fresh observation.
        # CONSENT-SAFE by construction: find_substitute excludes consequential
        # siblings, so no consent gate is ever bypassed. BOUNDS-SAFE: the same
        # check_effective_bounds verdict gates the sibling. One substitution per
        # capability per turn. Any actuator error → fall through to the marker.
        sub = await _try_substitute(
            failed_tool=name,
            failed_args=args,
            tool_registry=tool_registry,
            effective=effective,
            substituted_tags=substituted_tags,
            trace_id=state.trace_id,
        )
        if sub is not None:
            return sub
        # No route-around — prefix the rendered error with the structural marker so
        # the give-up judge (which sees only these rendered strings) can tell a
        # failed action from a successful one. Language-agnostic; the model still
        # reads a normal error message after the (invisible-ish) sentinel.
        from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

        return f"{TOOL_FAILED_MARKER}{tr.error or tr.output}"

    # Phase D — real-time persistence enforcer. Build a deliver-vs-giveup callback
    # the provider loop calls just before accepting a final answer. The provider
    # cannot reach the provider_registry; execute (which has services) can — so the
    # factory closes over services here. W1.T3: the give-up enforcer now covers ALL
    # turns (no interactive/depth gate) so cron/parliament/delegated sub-pipelines
    # are also caught, and the judge has a fallback tier (see build_persistence_check).
    persistence_check = build_persistence_check(state, get_services())

    # E2-S4 — budget governor: enforce the acting owl's effective caps (cost best-
    # effort, steps + time) once per ReAct iteration via on_iteration_complete.
    # No caps / unbounded owl → a no-op gate (every current turn unchanged).
    _services = get_services()
    try:
        _eff = compute_effective_bounds(state, _services.owl_registry)
    except Exception:  # noqa: BLE001 — budget is best-effort; never block the turn on bounds
        _eff = None
    _caps = _eff.caps if _eff is not None else ResourceCaps()
    # When caps is None (owl carries no caps axis) treat as all-None ResourceCaps.
    if _caps is None:
        _caps = ResourceCaps()

    class _MonotonicClock:
        def monotonic(self) -> float:
            return time.monotonic()

    _has_caps = any(
        c is not None for c in (_caps.max_steps, _caps.max_time_s, _caps.max_cost_usd)
    )
    if _has_caps:
        _governor = BudgetGovernor(
            _caps, cost_tracker=_services.cost_tracker, trace_id=state.trace_id,
            started_monotonic=time.monotonic(), clock=_MonotonicClock(),
        )
        _budget_cb = make_budget_callback(
            _governor, interactive=state.interactive, clarify=_services.clarify_gateway,
            session_id=state.session_id, channel=state.channel,
        )
    else:
        _budget_cb = None

    # Task 10 — steering closure: drain THIS turn's mailbox at each iteration
    # boundary and fold a coalesced [steering] user message into the loop. Reaches
    # its own turn via state.trace_id (== the turn's request_id) → the
    # process-wide TurnRegistry on services. Fail-safe: no registry / no turn /
    # empty mailbox → returns None (loop proceeds normally).
    #
    # Task 11 FOLLOW-UP SEAM (lost-steer finalize side, NOT wired here): the
    # registry now exposes `finalize_if_drained(request_id)` — re-check the mailbox
    # under the turn lock and CAS RUNNING→FINALIZING only when drained — for the
    # "execute terminal sequence" to loop on before the turn finalizes. It is NOT
    # called here because execute.py does NOT own the ReAct loop: the provider's
    # `complete_with_tools` drives every iteration internally and returns only the
    # FINAL text; execute makes a single `await`, so there is no per-iteration
    # terminal boundary in THIS function to fold a last-moment steer at. Moreover,
    # the RUNNING→FINALIZING→DONE lifecycle is currently NOT driven for interactive
    # turns at all (the orchestrator's _drain_next calls `deregister` directly,
    # never transitioning status), so there is no existing finalization line to
    # guard. Wiring finalize_if_drained correctly belongs at the point that DOES own
    # finalization — either (a) a provider-internal hook invoked at the loop's true
    # terminal boundary, or (b) the orchestrator's completion/_drain_next seam,
    # which must first introduce the FINALIZING transition. The enqueue side
    # (`try_steer`) and teardown (`drain_survivors`) are implemented + unit-tested,
    # and the end-to-end fold is covered by tests/pipeline/test_steering_fold_end_to_end.py.
    _steering_cb = make_steering_callback(_services.turn_registry, state.trace_id)

    def _compose_iter_cbs(
        cbs: list[IterationCallback],
    ) -> IterationCallback | None:
        """Compose ordered iteration callbacks into one that runs each in turn and
        concatenates any folded (non-None) messages (Task 9 splice contract). Side-
        effect-only callbacks return None and contribute nothing to the fold."""
        active = [c for c in cbs if c is not None]
        if not active:
            return None
        if len(active) == 1:
            return active[0]

        async def _composed(s: ReActIterationState) -> list[dict[str, Any]] | None:
            folded: list[dict[str, Any]] = []
            for c in active:
                part = await c(s)
                if part:
                    folded.extend(part)
            return folded or None

        return _composed

    t0 = time.monotonic()
    # Only forward persistence_check when it is actually enabled (interactive,
    # depth 0). Omitting the kwarg otherwise keeps the call backward-compatible
    # with every provider implementation (no new kwarg on the non-interactive path).
    #
    # B2 durable-react — when state.task_id is set this turn belongs to a durable
    # task: activate a DurableReActContext for the drive so the (dormant) S2
    # ledger_guard becomes live (side-effecting tools → exactly-once) and pass the
    # per-iteration checkpoint callback so each ReAct round is persisted. When
    # task_id is None (every non-durable turn) NONE of this runs and the call is
    # made EXACTLY as before (no context, no extra kwargs) — byte-for-byte.
    async def _call_default() -> tuple[str, list[dict[str, Any]]]:
        _extra: dict[str, Any] = {}
        if persistence_check is not None:
            _extra["persistence_check"] = persistence_check
        # Budget gate first (it may Raise to stop the loop), then steering fold.
        _default_cb = _compose_iter_cbs(
            [c for c in (_budget_cb, _steering_cb) if c is not None]
        )
        if _default_cb is not None:
            _extra["on_iteration_complete"] = _default_cb
        return await provider.complete_with_tools(
            user_text=state.input_text,
            system_text=state.system_prompt,
            tool_schemas=tool_schemas,
            tool_dispatcher=_dispatch,
            history=list(state.history),
            **_extra,
        )

    async def _call_durable(task_id: str) -> tuple[str, list[dict[str, Any]]]:
        # Imports are local so the default path never pays the durable cost.
        from stackowl.pipeline.durable.checkpoint_callback import (
            make_checkpoint_callback,
        )
        from stackowl.pipeline.durable.context import activate
        from stackowl.pipeline.durable.session import durable_session_for_state
        from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

        # 1. ENTRY
        owner_id = state.durable_owner_id or DEFAULT_PRINCIPAL_ID
        log.tasks.info(
            "[tasks] execute: durable drive entry",
            extra={"_fields": {
                "task_id": task_id,
                "owner_id": owner_id,
                "trace_id": state.trace_id,
                "owl": state.owl_name,
                "resume_iteration": state.durable_resume_iteration,
                "resuming": state.durable_resume_messages is not None,
            }},
        )
        # 2. DECISION — a durable task MUST have a real DbPool; fail loud rather
        #    than silently running a "durable" task non-durably (no ledger guard,
        #    no checkpoints = lost exactly-once + no resume).
        db = get_services().db_pool
        if db is None:
            log.tasks.error(
                "[tasks] execute: durable task but NO db_pool wired — refusing",
                extra={"_fields": {"task_id": task_id, "owner_id": owner_id,
                                   "trace_id": state.trace_id}},
            )
            raise RuntimeError(
                f"durable task {task_id!r} requested but no DbPool is available; "
                "refusing to run a durable task without its durability layer"
            )
        # Assemble the ledger/store/ctx via the shared factory so B3/B4 build the
        # durable scope identically (owner resolution + iteration seeding live
        # there, fixing the `or 0` resume-at-0 bug).
        session = durable_session_for_state(state, db)
        ctx = session.ctx
        cb = make_checkpoint_callback(ctx, session.store)
        # E2-S4 / Task 10 — compose in order: checkpoint the completed iteration
        # first (so a breached turn is still durably recorded and the resume seam
        # can replay from it on a Raise), THEN gate budget, THEN fold steering.
        # Checkpoint + budget return None (no fold); steering returns the
        # [steering] message. _compose_iter_cbs concatenates any folded messages
        # (Task 9 splice contract) so no callback's splice is silently lost.
        _iter_cb = _compose_iter_cbs(
            [c for c in (cb, _budget_cb, _steering_cb) if c is not None]
        )

        log.tasks.debug(
            "[tasks] execute: durable context built — activating",
            extra={"_fields": {"task_id": task_id, "owner_id": owner_id,
                               "start_iteration": ctx.iteration}},
        )
        # 3. STEP — drive the provider loop under the active durable context so the
        #    S2 ledger_guard is live and each iteration is checkpointed.
        with activate(ctx):
            _durable_extra: dict[str, Any] = {}
            if persistence_check is not None:
                _durable_extra["persistence_check"] = persistence_check
            result = await provider.complete_with_tools(
                user_text=state.input_text,
                system_text=state.system_prompt,
                tool_schemas=tool_schemas,
                tool_dispatcher=_dispatch,
                history=list(state.history),
                on_iteration_complete=_iter_cb,
                resume_messages=state.durable_resume_messages,
                resume_tool_calls=state.durable_resume_tool_calls,
                **_durable_extra,
            )
        # 4. EXIT
        log.tasks.info(
            "[tasks] execute: durable drive exit",
            extra={"_fields": {"task_id": task_id, "owner_id": owner_id,
                               "final_iteration": ctx.iteration,
                               "tool_calls": len(result[1])}},
        )
        return result

    try:
        if state.task_id is None:
            # DEFAULT PATH — all current traffic. Unchanged from prior behavior.
            final_text, raw_calls = await _call_default()
        else:
            final_text, raw_calls = await _call_durable(state.task_id)
    except DurableReplayUncertain as exc:
        # STRUCTURED PARK — the ledger returned `uncertain` (an `intent` row with
        # no matching commit): a prior attempt may have HALF-RUN a side effect, so
        # the guard refused to re-run it. This is NOT a transient failure — it must
        # be distinguishable so the B3 router can decide park-vs-retry rather than
        # blindly retrying. Mark the state parked AND record a structured marker in
        # errors (so the existing error-recording shape is preserved). The tool was
        # NOT re-run. Caught BEFORE the bare except below, which keeps handling all
        # other exceptions exactly as before.
        log.tasks.warning(
            "[tasks] execute: durable replay uncertain — parking task",
            exc_info=exc,
            extra={"_fields": {
                "trace_id": state.trace_id, "owl": state.owl_name,
                "task_id": exc.task_id, "step_index": exc.step_index,
                "tool": exc.tool_name,
            }},
        )
        marker = (
            f"durable:park:uncertain:task={exc.task_id}:"
            f"iteration={exc.step_index}:tool={exc.tool_name}"
        )
        return state.evolve(
            durable_parked=True,
            errors=(*state.errors, marker),
        )
    except TurnStopped as exc:
        # Cooperative STOP (concurrent-msg §5.3) — the steering callback honored the
        # turn's stop_requested FLAG at an iteration boundary and raised this to END
        # the loop gracefully (NEVER task.cancel() → no torn mid-tool state). Finalize
        # with a "stopped" chunk carrying any partial work the model produced so the
        # user sees the turn stopped cleanly rather than vanishing. Caught BEFORE the
        # bare except so a stop is a clean terminal, not a logged error.
        log.engine.info(
            "[pipeline] execute: turn stopped cooperatively — finalizing gracefully",
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name,
                               "request_id": exc.request_id,
                               "tool_calls": len(exc.tool_call_records)}},
        )
        _stopped_note = "[stopped: you asked me to stop — ending this turn here.]"
        _stopped_content = (
            f"{exc.partial_text}\n\n{_stopped_note}" if exc.partial_text else _stopped_note
        )
        _stopped_chunks = (ResponseChunk(
            content=_stopped_content, is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        ),)
        _stopped_raw: list[dict[str, Any]] = exc.tool_call_records
        _stopped_tool_records = tuple(
            ToolCall(
                tool_name=str(rc.get("name", "")),
                args=dict(rc.get("args") or {}),
                result=str(rc.get("result", "")),
                error=None,
                duration_ms=0.0,
            )
            for rc in _stopped_raw
        )
        return state.evolve(
            responses=(*state.responses, *_stopped_chunks),
            tool_calls=(*state.tool_calls, *_stopped_tool_records),
            errors=(*state.errors, f"turn:stopped:{exc.request_id}"),
        )
    except BudgetBreach as exc:
        log.engine.info(
            "[pipeline] execute: budget cap reached — stopping with partial",
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name,
                               "cap": exc.cap, "limit": exc.limit, "actual": exc.actual}},
        )
        note = f"\n\n[stopped: budget cap '{exc.cap}' reached (limit {exc.limit}, used {exc.actual})]"
        _breach_chunks = (ResponseChunk(
            content=(exc.partial_text + note), is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        ),)
        _breach_raw: list[dict[str, Any]] = exc.tool_call_records
        _breach_tool_records = tuple(
            ToolCall(
                tool_name=str(rc.get("name", "")),
                args=dict(rc.get("args") or {}),
                result=str(rc.get("result", "")),
                error=None,
                duration_ms=0.0,
            )
            for rc in _breach_raw
        )
        marker = f"budget:stop:{exc.cap}:limit={exc.limit}:actual={exc.actual}"
        return state.evolve(
            responses=(*state.responses, *_breach_chunks),
            tool_calls=(*state.tool_calls, *_breach_tool_records),
            errors=(*state.errors, marker),
        )
    except Exception as exc:
        log.engine.error(
            "[pipeline] execute: tool_loop failed",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        # LOAD-BEARING responses-only invariant (W2.T10): the floor ONLY ever ADDS
        # to `responses`. The original error STAYS in `errors` so the durable
        # status map / A2A status / parliament (all infer success from
        # error-absence) keep seeing this turn as FAILED — an honest message to
        # the user must never flip a real failure into a fake success.
        # `{attempts}` degrades to [] here: the provider's tool records died with
        # its stack frame (not attached to the exception), so the floor stays
        # honest from goal + error + the last partial response only.
        _prior = state.responses[-1].content if state.responses else ""
        floor = synthesize_floor(
            goal=state.input_text,
            error=str(exc),
            attempts=[],
            partial=_prior,
        )
        floor_chunk = ResponseChunk(
            content=floor,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
            is_floor=True,
        )
        return state.evolve(
            responses=(*state.responses, floor_chunk),
            errors=(*state.errors, f"execute: {type(exc).__name__}: {exc}"),
        )

    duration_ms = (time.monotonic() - t0) * 1000
    tool_records = tuple(
        ToolCall(
            tool_name=str(rc.get("name", "")),
            args=dict(rc.get("args") or {}),
            result=str(rc.get("result", "")),
            error=None,
            duration_ms=0.0,
        )
        for rc in raw_calls
    )
    chunks: tuple[ResponseChunk, ...] = ()
    if final_text:
        chunks = (ResponseChunk(
            content=final_text,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        ),)
    else:
        # Empty-final safety (W2.T10): loop exhaustion / an empty model wrap-up must
        # never hand the user zero chunks. Floor a non-empty honest chunk. This is
        # the NORMAL (no-error) exit — responses-only, errors stay untouched.
        floor = synthesize_floor(
            goal=state.input_text,
            error="",
            attempts=[],
            partial=state.responses[-1].content if state.responses else "",
        )
        chunks = (ResponseChunk(
            content=floor,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
            is_floor=True,
        ),)
    log.engine.info(
        "[pipeline] execute: tool_loop exit",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "owl": state.owl_name,
            "tool_calls": len(raw_calls),
            "duration_ms": duration_ms,
        }},
    )
    return state.evolve(
        responses=(*state.responses, *chunks),
        tool_calls=(*state.tool_calls, *tool_records),
    )


def _resolve_manifest(owl_name: str) -> OwlAgentManifest | None:
    """Best-effort lookup of an owl manifest; returns None on any miss."""
    services = get_services()
    registry = services.owl_registry
    if registry is None:
        log.engine.debug(
            "[pipeline] execute: no owl_registry — guard disabled",
            extra={"_fields": {"owl": owl_name}},
        )
        return None
    try:
        return registry.get(owl_name)
    except Exception as exc:
        log.engine.warning(
            "[pipeline] execute: owl manifest lookup failed — guard disabled",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name}},
        )
        return None


def _open_stream(
    provider: ModelProvider,
    manifest: OwlAgentManifest | None,
    messages: list[Message],
) -> AsyncIterator[str]:
    """Return a guarded stream when a manifest exists, else a raw provider stream."""
    if manifest is None:
        return provider.stream(messages, model="")
    guard = OwlResourceGuard(manifest)
    return guard.stream(provider, messages, model="")


def _select_tool_provider(
    registry: ProviderRegistry,
    services: object,
    state: PipelineState,
) -> ModelProvider:
    """Resolve the ModelProvider for the tool-use loop.

    Precedence (highest → lowest):
    1. Owl manifest ``provider_name`` pin — if set and registered, use it directly.
       On ProviderNotFoundError warn and fall through to tier routing.
    2. Desired tier = get_session_tier(session_id) OR manifest.model_tier OR "powerful".
       Session pref beats manifest; manifest beats default.
    3. Resolve via registry.resolve_tier_with_fallback(desired_tier) — circuit-aware
       (falls back if the tier provider's circuit is OPEN).
    """
    log.engine.debug(
        "[pipeline] execute: _select_tool_provider: entry",
        extra={"_fields": {"owl": state.owl_name, "session": state.session_id}},
    )

    # --- Step 0: A provider registered under the owl's own name wins (a
    # per-owl provider binding). This is the most specific pin. ---
    try:
        provider = registry.get(state.owl_name)
        log.engine.info(
            "[pipeline] execute: tool provider selected",
            extra={"_fields": {
                "owl": state.owl_name,
                "chosen_provider_name": state.owl_name,
                "source": "owl_named_provider",
            }},
        )
        return provider
    except ProviderNotFoundError:
        pass  # no per-owl provider — fall through to manifest/tier routing

    # --- Step 1: Fetch manifest (best-effort) ---
    manifest: OwlAgentManifest | None = None
    owl_reg = getattr(services, "owl_registry", None)
    if owl_reg is not None:
        try:
            manifest = owl_reg.get(state.owl_name)
        except Exception as exc:
            # Expected for an unknown owl; logged (never silent) so a registry
            # fault is distinguishable from a benign not-found.
            log.engine.debug(
                "[pipeline] execute: owl manifest lookup failed — tier routing only",
                exc_info=exc,
                extra={"_fields": {"owl": state.owl_name}},
            )
            manifest = None

    # --- Step 2: Explicit provider pin ---
    if manifest is not None and manifest.provider_name:
        try:
            provider = registry.get(manifest.provider_name)
            log.engine.info(
                "[pipeline] execute: tool provider selected",
                extra={"_fields": {
                    "owl": state.owl_name,
                    "desired_tier": manifest.model_tier,
                    "chosen_provider_name": manifest.provider_name,
                    "source": "manifest_pin",
                }},
            )
            return provider
        except ProviderNotFoundError:
            log.engine.warning(
                "[pipeline] execute: manifest provider_name not registered — falling back to tier",
                extra={"_fields": {"owl": state.owl_name, "provider_name": manifest.provider_name}},
            )

    # --- Step 3: Determine desired tier (session pref > manifest > default) ---
    session_tier = get_session_tier(state.session_id)
    if session_tier:
        desired = session_tier
        tier_source = "session"
    elif manifest is not None and manifest.model_tier:
        desired = manifest.model_tier
        tier_source = "manifest"
    else:
        desired = "powerful"
        tier_source = "default"
        if manifest is None:
            log.engine.warning(
                "[pipeline] execute: unknown owl or no manifest — defaulting to 'powerful' tier",
                extra={"_fields": {"owl": state.owl_name}},
            )

    # --- Step 4: Resolve by tier — circuit-aware (falls back if the tier provider's
    # circuit is OPEN; the pins above are honored as-is). ---
    provider, degraded_from = registry.resolve_tier_with_fallback(desired)
    if degraded_from is not None:
        recovery_context.record_recovery(
            kind="provider_fallback", failed=degraded_from,
            recovered_via=provider.name, user_visible=True,
        )
    log.engine.info(
        "[pipeline] execute: tool provider selected",
        extra={"_fields": {
            "owl": state.owl_name,
            "desired_tier": desired,
            "chosen_provider_name": getattr(provider, "name", type(provider).__name__),
            "source": tier_source,
        }},
    )
    return provider


async def run(state: PipelineState) -> PipelineState:
    """Stream tokens from the assigned provider and build state.responses."""
    log.engine.info(
        "[pipeline] execute: entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
    )
    services = get_services()
    registry = services.provider_registry
    tool_registry = services.tool_registry
    if registry is None:
        log.engine.warning("[pipeline] execute: no provider_registry — pass-through")
        return state

    try:
        provider = _select_tool_provider(registry, services, state)
    except AllProvidersUnavailableError as exc:
        log.engine.error(
            "[pipeline] execute: all providers unavailable — flooring",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, f"execute: AllProvidersUnavailableError: {exc}"),
        )

    # Tool loop path: use complete_with_tools() when tools are available
    if tool_registry is not None and tool_registry.all():
        return await _run_with_tools(state, provider, tool_registry)

    messages: list[Message] = [*state.history, Message(role="user", content=state.input_text)]
    if state.system_prompt:
        messages = [Message(role="system", content=state.system_prompt), *messages]

    manifest = _resolve_manifest(state.owl_name)
    stream_iter = _open_stream(provider, manifest, messages)

    t0 = time.monotonic()
    chunks: list[ResponseChunk] = []
    chunk_index = 0
    try:
        async for text in stream_iter:
            chunk = ResponseChunk(
                content=text,
                is_final=False,
                chunk_index=chunk_index,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            chunks.append(chunk)
            chunk_index += 1
    except OwlTimeoutError as exc:
        log.engine.warning(
            "[pipeline] execute: owl timeout",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            responses=(*state.responses, *chunks),
            errors=(*state.errors, f"execute: OwlTimeoutError: {exc}"),
        )
    except OwlConcurrencyError as exc:
        log.engine.warning(
            "[pipeline] execute: owl concurrency limit",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, f"execute: OwlConcurrencyError: {exc}"),
        )
    except OwlTokenLimitError as exc:
        # Token-limit truncation is intentional — collected chunks stay in state.
        log.engine.warning(
            "[pipeline] execute: token limit reached — truncated",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
    except Exception as exc:
        log.engine.error(
            "[pipeline] execute: provider stream failed",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(errors=(*state.errors, f"execute: {type(exc).__name__}: {exc}"))

    duration_ms = (time.monotonic() - t0) * 1000
    log.engine.info(
        "[pipeline] execute: exit",
        extra={
            "_fields": {
                "trace_id": state.trace_id,
                "owl": state.owl_name,
                "chunks": len(chunks),
                "duration_ms": duration_ms,
                "guarded": manifest is not None,
            }
        },
    )
    return state.evolve(responses=(*state.responses, *chunks))
