"""Pipeline step 4: execute — stream from ModelProvider through OwlResourceGuard."""

from __future__ import annotations

import asyncio
import functools
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS, ResourceCaps
from stackowl.exceptions import (
    AllProvidersUnavailableError,
    BudgetBreach,
    DurableReplayUncertain,
    OwlConcurrencyError,
    OwlTimeoutError,
    OwlTokenLimitError,
    ToolUseUnsupportedError,
    TurnStopped,
)
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.interaction.reversibility_resolver import (
    Decision,
    Reversibility,
    ReversibilityResolver,
    reversibility_resolver_enabled,
)
from stackowl.owls.guards import OwlResourceGuard
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.pipeline.authz_compose import compute_effective_bounds
from stackowl.pipeline.budget import BudgetGovernor, make_budget_callback
from stackowl.pipeline.budget.callback import resolve_clarify_wait_timeout
from stackowl.pipeline.budget.human_wait import current_human_wait_seconds
from stackowl.pipeline.context_budget import HARD_TOOL_COUNT_CAP, RESPONSE_RESERVE_TOKENS
from stackowl.pipeline.persistence import TOOL_FAILED_MARKER
from stackowl.pipeline.progress.emitter import emit_start as emit_progress_start
from stackowl.pipeline.progress.emitter import make_progress_callback
from stackowl.pipeline.provider_select import (
    ToolProviderChoice,
    select_tool_provider_plan,
)
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState, StepError, ToolCall
from stackowl.pipeline.step_error import format_step_error
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.providers.base import Message, ModelProvider
from stackowl.providers.escalation_signal import clear_escalation, request_escalation
from stackowl.providers.model_window import DEFAULT_WINDOW_FALLBACK, resolve_window
from stackowl.providers.react_callback import ReActIterationState
from stackowl.tools.child_exclusion import CHILD_EXCLUDED_TOOLS
from stackowl.tools.registry import ToolRegistry
from stackowl.tools.verification import is_trustworthy_success

if TYPE_CHECKING:
    from stackowl.gateway.turn_registry import TurnRegistry
    from stackowl.pipeline.services import StepServices
    from stackowl.providers.react_callback import IterationCallback
    from stackowl.skills.store import SkillIndexStore
    from stackowl.tools.base import ToolResult

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
# SEC-3 — the canonical set now lives in stackowl.tools.child_exclusion (shared with
# the per-tool self-defense). Aliased here under the long-standing name so this
# module's schema filter + dispatch-seam re-check keep using one source of truth.
_CHILD_EXCLUDED_TOOLS = CHILD_EXCLUDED_TOOLS


def _est_tokens(text: str | None) -> int:
    """Cheap token estimate (~4 chars/token). Never raises."""
    return (len(text) // 4) if text else 0


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Most-recent assistant text in the live message list (partial work on stop)."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return str(m["content"])
    return ""


# FR-10 — a clean turn (no failed tool, non-empty draft, ≥1 tool tried OR a long
# enough draft) skips the LLM give-up judge entirely. This is deliberately generous
# toward RUNNING the judge on ambiguous short answers — the dangerous direction is
# skipping a real give-up, not the extra judge call on a real short one-liner.
_SHORT_DRAFT_CHARS = 150


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

    # Per-turn memory: once a judge that COULD vet ruled give-up this turn, a later
    # fail-open (empty/unparseable judge output — the 2026-06-23 reasoning-model
    # truncation) must NOT silently upgrade that give-up into a shipped, unvetted
    # draft. The closure is built once per turn and reused across provider
    # iterations, so this flag spans the whole turn.
    seen_giveup = False
    # PA2 — fire the unvetted-substantive nudge AT MOST ONCE per turn. `tools_tried`
    # and the consequential tally do not change between re-answer passes, so without
    # this latch the block would re-fire every pass until the nudge budget drains —
    # spurious round-trips on an already-honest read+summarise turn. One chance, then
    # accept (the same one-shot discipline as `seen_giveup`, opposite resolution).
    pa2_nudged = False

    async def _persistence_check(draft: str, tools_tried: list[str]) -> str | None:
        nonlocal seen_giveup, pa2_nudged
        # FR-10 — gate the judge (and its fallback tier) to only the turns that
        # need it: a failed tool call this turn (ledger-recorded), an empty draft,
        # or a refusal-shaped proxy (0 tools tried AND a suspiciously short draft —
        # structural, not keyword-based). A clean turn returns None here exactly as
        # a judge ruling "delivered" would — no LLM call, no preg/provider touch,
        # no seen_giveup/pa2_nudged mutation.
        has_failed_tool = any(not o.success for o in tool_outcome_ledger.get_outcomes())
        empty_draft = not draft.strip()
        refusal_shaped = not tools_tried and len(draft.strip()) < _SHORT_DRAFT_CHARS
        if not (has_failed_tool or empty_draft or refusal_shaped):
            log.engine.debug(
                "[pipeline] execute: persistence judge skipped — clean turn, no LLM call",
                extra={"_fields": {
                    "trace_id": state.trace_id,
                    "tools_tried_count": len(tools_tried),
                    "draft_len": len(draft),
                }},
            )
            return None
        preg = services.provider_registry
        # PRIMARY judge — resolve (injected or "fast" tier) and rule. judge_delivery
        # is itself fail-OPEN: on a provider/parse error it returns
        # (True, JUDGE_ERROR_REASON) instead of raising. So a primary failure shows
        # up EITHER as a raised exception (provider lookup) OR as that sentinel —
        # both route to the fallback tier rather than silently accepting a give-up.
        # Judge tier is config-driven (default "standard"): the smallest tier is a
        # false economy here — a thinking model rambles for thousands of tokens
        # (slow) and rules give-up unreliably (wrong), forcing premature
        # escalations. Resolved lazily so a hot settings reload is picked up.
        _settings = getattr(services, "settings", None)
        _judge_tier = getattr(_settings, "judge_tier", "standard") or "standard"
        try:
            judge = primary if primary is not None else (
                preg.get_with_cascade(_judge_tier) if preg is not None else None
            )
            if judge is None:  # no registry → cannot judge (fail open)
                delivered, reason = True, JUDGE_ERROR_REASON
            else:
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
                if fb is None:  # no fallback available — fail open
                    delivered, reason = True, JUDGE_ERROR_REASON
                else:
                    delivered, reason = await judge_delivery(
                        fb, state.input_text, draft, tools_tried
                    )
            except Exception as exc2:  # fallback lookup also raised — final fail OPEN
                log.engine.error(
                    "[pipeline] execute: fallback persistence judge also failed",
                    exc_info=exc2,
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
                delivered, reason = True, JUDGE_ERROR_REASON

        could_vet = reason != JUDGE_ERROR_REASON

        if not delivered and could_vet:
            # A genuine give-up verdict — nudge, and remember it for this turn.
            seen_giveup = True
            log.engine.info(
                "[pipeline] execute: persistence judge ruled give-up — nudging",
                extra={"_fields": {"trace_id": state.trace_id, "reason": reason[:120]}},
            )
            return PERSISTENCE_DIRECTIVE

        if delivered and could_vet:
            # Genuinely vetted as delivered — any earlier give-up is resolved.
            return None

        # Fail-open: neither judge could vet (empty/unparseable/unavailable). Never
        # let that ship a draft after a real give-up was seen this turn — keep
        # nudging (the nudge ceiling + budget backstop bound the loop, then the
        # honest floor delivers).
        if seen_giveup:
            log.engine.warning(
                "[pipeline] execute: persistence judge failed open after a give-up "
                "— preserving give-up (nudging), not shipping an unvetted draft",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return PERSISTENCE_DIRECTIVE

        # PA2 — close the residual fail-OPEN hole. We only reach here when the judge
        # could NOT vet on EVERY pass this turn (`could_vet` was never True) and no
        # give-up was ever flagged. Historically that always accepted, shipping a draft
        # nothing vetted. Distinguish two un-vetted cases by the signals already on the
        # turn ledger (no new heuristic, no keyword list):
        #   * EFFECTFUL work (write/consequential tally non-zero) → the consequential
        #     give-up floor (`has_consequential_snapshot`) is the backstop; accept here
        #     exactly as before so the floor — not a doubled nudge — resolves it.
        #   * A CLEAN turn (no tool work) had nothing to deliver-or-give-up → accept, so
        #     a judge outage never starts nudging ordinary conversation.
        #   * SUBSTANTIVE non-effectful work (tools ran, none effectful) the judge never
        #     vetted is the genuinely-unsafe slice with NO backstop → fail CLOSED: nudge
        #     once toward an honest, grounded answer. The provider's nudge ceiling (2) +
        #     budget governor bound the loop, then the never-empty floor delivers.
        # The latch makes "nudge once" literally true: after one PA2 nudge the model
        # got its chance, so a still-unvettable re-answer pass falls through to accept
        # rather than burning the remaining nudge budget on the same honest draft.
        cons_failures, cons_successes = tool_outcome_ledger.consequential_tally()
        backstopped = (cons_failures + cons_successes) > 0
        if tools_tried and not backstopped and not pa2_nudged:
            pa2_nudged = True
            log.engine.warning(
                "[pipeline] execute: persistence judge never vetted a substantive "
                "non-effectful turn — nudging once toward an honest grounded answer",
                extra={"_fields": {
                    "trace_id": state.trace_id,
                    "tools_tried": len(tools_tried),
                }},
            )
            return PERSISTENCE_DIRECTIVE
        # Accept fall-through — record which branch resolved a judge-down turn so the
        # logs are not blind: effectful (floor backstops) / clean / already-nudged.
        _branch = (
            "effectful-backstop" if backstopped
            else "already-nudged" if pa2_nudged
            else "clean-turn" if not tools_tried
            else "accepted"
        )
        log.engine.debug(
            "[pipeline] execute: persistence judge unvettable — accepting draft",
            extra={"_fields": {"trace_id": state.trace_id, "branch": _branch}},
        )
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
        # draining so we observe any co-arriving steer before we stop. REACT-6/F033:
        # those drained steers are NOT discarded — the callback already removed them
        # from the mailbox, so the completion-seam survivor drain would find nothing.
        # Carry them on TurnStopped so the execute finalize seam re-routes them as
        # queued-new turns (the same path survivors take). FLAG only — we raise a
        # controlled TurnStopped, NEVER task.cancel().
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
                drained_steers=drained,
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
    locale: str = "en",
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

    try:
        # F-6 — a capability class can have MULTIPLE ranked siblings. Loop over them,
        # marking only the TRIED sibling as exhausted (NOT the whole capability tag),
        # so one flaky sibling no longer surrenders the entire class while other
        # ranked candidates remain. The tag is added to ``substituted_tags`` ONLY on a
        # trustworthy success below (one SUCCESSFUL substitution per class per turn);
        # a sibling that fails just advances to the next candidate. ``tried_siblings``
        # is folded into the bounds predicate so ``find_substitute`` skips an
        # already-tried sibling on the next pass (it is otherwise tag-gated).
        tried_siblings: set[str] = set()
        while True:
            match = find_substitute(
                failed_tool,
                failed_args,
                registry=tool_registry,
                in_bounds=lambda n: (
                    n not in tried_siblings and check_effective_bounds(effective, n) is None
                ),
                already_substituted=substituted_tags,
            )
            if match is None:
                return None
            sibling_name, sibling_args = match
            # F-5 — mark the candidate tried BEFORE running it so that if its
            # actuator MACHINERY breaks below, the next find_substitute pass skips
            # it (in_bounds excludes tried_siblings) and we advance to the next
            # ranked candidate instead of looping on the broken one.
            tried_siblings.add(sibling_name)
            # F-5 — the per-sibling actuator path is guarded INDEPENDENTLY of the
            # whole loop: a machinery fault running ONE sibling (a ledger-guard
            # error, an outcome-ledger raise, etc. — distinct from the tool merely
            # returning failure, which Tool.__call__ already wraps into a
            # ToolResult) advances to the NEXT candidate. Only when no candidate
            # remains (match is None above) do we surrender. This distinguishes
            # "no sibling exists" from "this sibling's actuator broke".
            try:
                sib = tool_registry.get(sibling_name)
                if sib is None:  # raced/unregistered — try the next ranked candidate
                    log.engine.warning(
                        "[pipeline] execute: substitute sibling vanished from registry",
                        extra={"_fields": {"sibling": sibling_name, "trace_id": trace_id}},
                    )
                    continue
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
                # partial binds sib + args EAGERLY (a fresh zero-arg op each iteration)
                # so the loop's rebinding never leaks into a deferred closure (B023).
                sib_result = await ledger_guard(
                    sibling_name, sibling_args, sib.manifest.action_severity,
                    functools.partial(sib, **sibling_args),
                )
                tool_outcome_ledger.record_tool_outcome(
                    name=sibling_name, action_severity=sib.manifest.action_severity,
                    success=sib_result.success,
                    side_effect_committed=sib_result.side_effect_committed,
                    # B4a — the substitute is held to the SAME reality check as the primary:
                    # a sibling that also CLAIMED success but produced nothing (verified=False)
                    # is not a route-around, it is a second false win.
                    verified=sib_result.verified,
                    effect_class=sib.manifest.effect_class,  # TS3 — carry the durable-effect class
                )
                if not is_trustworthy_success(sib_result.success, sib_result.verified):
                    log.engine.info(
                        "[pipeline] execute: substitute sibling also failed — trying next candidate",
                        extra={"_fields": {
                            "failed_tool": failed_tool, "sibling": sibling_name, "trace_id": trace_id,
                        }},
                    )
                    continue
                return _record_substitution_success(
                    failed_tool=failed_tool,
                    sib=sib,
                    sibling_name=sibling_name,
                    sib_output=sib_result.output,
                    substituted_tags=substituted_tags,
                    recovery_context=recovery_context,
                    locale=locale,
                    trace_id=trace_id,
                )
            except Exception as exc:  # noqa: BLE001 — one broken sibling != surrender
                # F-5 — this sibling's actuator broke (already in tried_siblings).
                # Advance to the next ranked candidate before giving up.
                log.engine.error(
                    "[pipeline] execute: substitute sibling actuator raised — trying next candidate",
                    exc_info=exc,
                    extra={"_fields": {
                        "failed_tool": failed_tool, "sibling": sibling_name, "trace_id": trace_id,
                    }},
                )
                continue
    except Exception as exc:  # noqa: BLE001 — the actuator must never crash the turn
        # Outer guard for the loop scaffolding itself (e.g. find_substitute raising):
        # with no live sibling identity to skip, honest surrender is correct.
        log.engine.error(
            "[pipeline] execute: self-heal substitution actuator failed — falling through",
            exc_info=exc,
            extra={"_fields": {"failed_tool": failed_tool, "trace_id": trace_id}},
        )
        return None


def _record_substitution_success(
    *,
    failed_tool: str,
    sib: Any,
    sibling_name: str,
    sib_output: str,
    substituted_tags: set[str],
    recovery_context: Any,
    locale: str,
    trace_id: str,
) -> str:
    """Finalize a trustworthy substitution: record the recovery, mark the class
    exhausted for this turn, and return the sibling's output prefixed with a
    neutral localized note. Extracted so the F-6 candidate loop stays readable."""
    from stackowl.setup.localize import localize_format

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
    # F030/REACT-4 — localize the user-facing note from the turn's resolved
    # language (state.language, threaded as `locale`); localize_format
    # en-fallbacks for any uncatalogued language. Never the hardcoded "en".
    note = localize_format(
        "self_heal_substituted", locale, failed=failed_tool, sibling=sibling_name,
    )
    log.engine.info(
        "[pipeline] execute: self-heal substitution succeeded",
        extra={"_fields": {
            "failed_tool": failed_tool, "sibling": sibling_name,
            "tag": tag, "trace_id": trace_id,
        }},
    )
    return f"{note}\n{sib_output}"


def _is_transient_failure(tr: ToolResult) -> bool:
    """F-7 / ADR-2 — classify a GENUINE tool failure (success=False) as transient.

    DELEGATES to the RecoveryActuator's single classifier
    (:func:`stackowl.pipeline.recovery_actuator.is_transient_result`) so the transient
    vocabulary lives with the one recovery authority instead of in this loop. Behavior is
    unchanged (the classifier is the relocated body of this function): a transient failure
    (dropped connection, reset socket, locked DB, closed pipe) can self-heal on a second
    attempt; a deterministic failure (bad input, missing capability, refusal) cannot. The
    execute-loop B4 ladder is thus the actuator's tool-dispatch caller (nothing removed)."""
    from stackowl.pipeline.recovery_actuator import is_transient_result

    return is_transient_result(tr)


# REACT-1/F032+F090 — hard ceiling on the FALLBACK window probe. The steady path
# never reaches it (assemble stamps state.model_window). It only fires when a turn
# reaches _run_with_tools WITHOUT assemble having run (e.g. a direct/system route),
# where a live ollama probe on the hot tool-loop entry must never hang the turn.
_WINDOW_PROBE_DEADLINE_S = 5.0

# REACT-5/F061 — per-tool execution DEADLINE (seconds). Bounds how long a single
# in-flight tool awaitable may run so a cooperative /stop is never blocked longer
# than this by one long tool: on timeout asyncio.wait_for cancels the TOOL's own
# coroutine (NOT the turn task), the loop observes a failed outcome and proceeds to
# the next iteration boundary where the stop flag is honored. Generous so it bounds
# the pathology without truncating a legitimately long tool; host-scalable (a beefier
# host can widen it) — NOT pinned to the dev box. This value IS the documented
# upper bound on stop latency contributed by a single in-flight tool.
_TOOL_DEADLINE_S = 180.0

# Incident P2 — same-tool repeated-failure circuit breaker. After this many
# CONSECUTIVE genuine execution failures of the SAME tool within one turn, the
# tool is bounced for the rest of the turn so a weak model cannot spiral on it
# (the pictures-overclaim incident: 9 failing `shell` calls burned budget to the
# 120s wall). One below LoopGuard's identical-args break_at=4 because this
# breaker's scope is broader (any args, by tool name). Host-agnostic fixed N —
# never tuned to a model/box (see feedback_never_pull_models_local_jetson).
SAME_TOOL_FAILURE_THRESHOLD = 3


async def _resolve_execute_window(state: PipelineState, provider: ModelProvider) -> int:
    """Resolve THIS turn's context window for the tool-loop budget — single probe.

    REACT-1/F032+F090:
      * STEADY path — assemble already resolved and stamped ``state.model_window``;
        return it directly so execute issues NO second probe (resolve_window's own
        memoization would make it a cache hit anyway, but reading state skips the
        call entirely and keeps the single-probe guarantee testable).
      * FALLBACK path — a route reached _run_with_tools without assemble (model_window
        is None). Issue ONE bounded probe under an explicit deadline; on timeout or
        any error fall back to the safe default window. resolve_window never raises,
        so the wait_for timeout is the only failure mode we add here.
    """
    if state.model_window is not None:
        return state.model_window
    cfg = getattr(provider, "_config", None)
    log.engine.debug(
        "[pipeline] execute: no stamped window — bounded fallback probe",
        extra={"_fields": {"trace_id": state.trace_id, "deadline_s": _WINDOW_PROBE_DEADLINE_S}},
    )
    try:
        return await asyncio.wait_for(
            resolve_window(
                provider_name=getattr(provider, "name", "") or "",
                base_url=cfg.base_url if cfg is not None else None,
                model=(cfg.default_model if cfg is not None else "") or "",
                context_chars=(cfg.context_chars if cfg is not None else None),
                protocol=getattr(provider, "protocol", "") or "",
            ),
            timeout=_WINDOW_PROBE_DEADLINE_S,
        )
    except TimeoutError as exc:
        log.engine.warning(
            "[pipeline] execute: window probe exceeded deadline — safe default window",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "window": DEFAULT_WINDOW_FALLBACK}},
        )
        return DEFAULT_WINDOW_FALLBACK


_EFFECTFUL_SEVERITIES = {"write", "consequential"}
# Recovery kinds that BRIDGE a failed effectful attempt to an achieved one, so the
# honest floor treats the original failure as recovered (not an unachieved goal):
# "substitution" (a sibling produced the result) and B4a "retry" (the same tool
# succeeded on a second, verified attempt).
_BRIDGING_RECOVERY_KINDS = {"substitution", "retry"}

# Local-workspace FILE-MUTATION tools. These mutate the local filesystem and are
# NEVER delivered out to the user — their success must not mask an unachieved
# consequential goal at a budget cap. EVERYTHING ELSE that succeeds (consequential
# sends, delegations `delegate_task`/`sessions_*`, builds) is a GOAL-RELEVANT /
# delivered success and disarms the floor. (`write`-severity boundary-crossing
# dispatches like `delegate_task` are deliberately NOT here — they cross the boundary
# OUT, so they count as delivered work.) Keyed on tool identity (a name set), NOT on
# prose keywords. If these tools ever gain a clean `capability_tag` (e.g.
# `filesystem.write`), prefer keying on that attribute over this set.
_LOCAL_FILE_MUTATION_TOOLS = frozenset({
    "write_file",   # io/write_file.py
    "edit",         # io/edit.py
    "apply_patch",  # io/apply_patch.py
    "undo_write",   # io/undo_store.py
})


def _snapshot_consequential(state: PipelineState) -> PipelineState:
    """REACT-7/F099 — stamp the turn's consequential tally + bridged set onto state.

    Read the turn-scoped ledger/recovery ContextVars (caller guarantees they are
    still bound) and carry the result on immutable state so the honest giveup floor
    reads the snapshot rather than depending on the bind() lifetime spanning the
    floor call. Never raises — a snapshot failure leaves the live-ledger path intact
    (consequential_snapshot_taken stays False)."""
    try:
        outcomes = tool_outcome_ledger.get_outcomes()
        failures = tuple(
            o.name for o in outcomes
            if tool_outcome_ledger.is_effectful_failure(
                o.action_severity, o.success, o.side_effect_committed, o.verified,
            )
        )
        # B2 — a verified=False effect is NOT a success (it was claimed, never observed),
        # so it neither counts here nor disarms the floor below.
        successes = tuple(
            o.name for o in outcomes
            if o.action_severity in _EFFECTFUL_SEVERITIES and o.success and o.verified is not False
        )
        # GOAL-RELEVANT subset: effectful successes MINUS local-workspace file
        # mutations. A pure local file write (write_file / edit / apply_patch /
        # undo_write) is incidental — it never delivers anything to the user, so it
        # must not disarm the honest floor when a consequential goal was unachieved at a
        # budget cap. Everything else effectful — consequential sends AND boundary-
        # crossing `write` dispatches (delegate_task / sessions_*) — is delivered work.
        # Used by the honest floor ONLY on a budget-cap cutoff; clean stops keep reading
        # the full `successes` tuple above.
        delivered = tuple(
            o.name for o in outcomes
            if o.action_severity in _EFFECTFUL_SEVERITIES
            and o.success
            and o.verified is not False
            and o.name not in _LOCAL_FILE_MUTATION_TOOLS
        )
        recovered = tuple(
            e.failed for e in recovery_context.get_recovery()
            if e.kind in _BRIDGING_RECOVERY_KINDS and e.recovered_via
        )
        # ADR-T2 / TS3 — names of tools that declared a durable EFFECT (effect_class
        # set: creates_persistent_entity / sends_message / schedules) whose result was
        # NOT MEASURED verified==True. DEFAULT-DENY: verified∈{False, None(unknown)} or
        # a plain failure all qualify — absence of a verified receipt = unproven effect.
        # The ledger-driven overclaim veto reads this off immutable state (the live
        # ContextVar may be unbound by the time the gate runs) and floors an affirmative
        # non-floor draft that claims an effect we cannot prove. Keys on effect_class
        # PRESENCE, never on the answer prose.
        unverified_effects = tuple(
            o.name for o in outcomes
            if o.effect_class is not None and o.verified is not True
        )
        return state.evolve(
            consequential_failures=failures,
            consequential_successes=successes,
            delivered_successes=delivered,
            recovered_consequential=recovered,
            unverified_effects=unverified_effects,
            consequential_snapshot_taken=True,
        )
    except Exception as exc:  # B5 — never break the turn; fall back to the live ledger
        log.engine.error(
            "[pipeline] execute: consequential snapshot failed — floor reads live ledger",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state


def _circuit_open_refusal(name: str) -> str:
    """Stable, model-readable refusal for a tool whose same-tool failure breaker
    tripped this turn (incident P2). Steers the model to change approach or stop;
    carries NO case-specifics. NOT prefixed with TOOL_FAILED_MARKER — a bounce is
    containment, not a tool failure, so the give-up judge must not read it as a
    failed consequential action (mirrors the denied_this_run bounce)."""
    return (
        f"The action '{name}' has failed repeatedly this turn and is no longer "
        f"available. Do not call it again — try a different approach, or if no "
        f"alternative remains, stop and tell the user what you could not do."
    )


def _approach_signature(name: str, args: dict[str, object]) -> str:
    """ADR-5 MOVE 3 — a stable, order-independent identity for ONE attempted approach
    (a tool name + its inputs) so an EXACT repeat within the turn is recognisable.
    Canonical JSON (sorted keys, ``default=str`` for non-JSON values) is deterministic
    across dict ordering; a NUL joiner avoids name/arg collisions. Never raises — a
    signature failure must not break dispatch."""
    try:
        canon = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — fall back to a repr; never break the call
        canon = repr(sorted((str(k), repr(v)) for k, v in args.items()))
    return f"{name}\x00{canon}"


def _repeated_approach_refusal(name: str) -> str:
    """ADR-5 MOVE 3 — stable, model-readable steer for an EXACT approach (this tool with
    these same inputs) that already failed earlier THIS turn. Like the circuit-breaker
    bounce this is CONTAINMENT, not a tool failure: it carries NO TOOL_FAILED_MARKER (so
    the give-up judge never reads it as a failed consequential action) and records
    NOTHING in the outcome ledger. Steers the model to change the approach."""
    return (
        f"You already tried '{name}' with these exact inputs earlier this turn and it "
        f"failed. Do not repeat the same approach — change the inputs or use a different "
        f"tool, or stop and tell the user what you could not do."
    )


def reset_ledger_for_tier_escalation(from_tier: str, to_tier: str, *, trace_id: str = "") -> None:
    """Reset turn-scoped state between discarded escalation attempts.

    Install a FRESH empty tool-outcome ledger (``bind()`` sets the ContextVar to
    ``()``) so a discarded weak attempt's tool failures don't poison the NEXT tier's
    give-up floor, and record the machinery recovery (kind ``tier_escalation``). The
    backend's outer ledger token still governs teardown at turn end; this only clears
    accumulation for the next attempt. Never raises — an escalation must never be
    aborted by bookkeeping.
    """
    try:
        tool_outcome_ledger.bind()  # set the ledger ContextVar to empty for the next tier
        recovery_context.record_recovery(
            kind="tier_escalation", failed=from_tier, recovered_via=to_tier,
            user_visible=False,
        )
        log.engine.info(
            "[pipeline] execute: tier escalation — reset ledger for the next attempt",
            extra={"_fields": {"trace_id": trace_id, "from_tier": from_tier, "to_tier": to_tier}},
        )
    except Exception as exc:  # noqa: BLE001 — never abort an escalation on bookkeeping
        log.engine.error(
            "[pipeline] execute: tier-escalation reset failed — continuing",
            exc_info=exc, extra={"_fields": {"trace_id": trace_id}},
        )


async def _run_with_tools(
    state: PipelineState,
    choice: ToolProviderChoice | ModelProvider,
    tool_registry: ToolRegistry,
) -> PipelineState:
    """Execute the provider's tool loop and return updated state.

    ``choice`` carries the resolved tool-loop provider PLUS the escalation plan: a
    PINNED choice (owl-named provider / manifest pin / explicit session tier) runs
    that provider directly; a non-pinned choice starts at ``"fast"`` and escalates
    fast→…→``choice.ceiling_tier`` through the LLMGateway when the weak model leaks
    an unparsed tool call (or the model itself emits ESCALATE).

    Back-compat: a bare ``ModelProvider`` (the historical ``_run_with_tools(state,
    provider, ...)`` direct-call contract — used by many integration tests and the
    durable/direct path) is adapted to a PINNED choice, i.e. called directly with no
    escalation, byte-identical to the prior behaviour.
    """
    if not isinstance(choice, ToolProviderChoice):
        choice = ToolProviderChoice(provider=choice, ceiling_tier="powerful", pinned=True)
    provider = choice.provider
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
    _fixed_cost = _est_tokens(state.system_prompt) + sum(
        _est_tokens(getattr(m, "content", "")) for m in state.history
    )
    # Configurable per-turn tool-count cap (OrchestratorSettings.tool_count_cap):
    # weak/quantized models derail when offered too many tools. Default 40 keeps the
    # presented set byte-identical; lower it to lean the roster for weak models.
    _svc_settings = get_services().settings
    _max_tools = (
        _svc_settings.orchestrator.tool_count_cap
        if _svc_settings is not None
        else HARD_TOOL_COUNT_CAP
    )

    async def build_tool_schemas(prov: ModelProvider) -> list[dict[str, object]]:
        """Build the presented tool schemas for ONE provider's protocol + window.

        Extracted so the LLMGateway can REBUILD the schemas per escalation tier (a
        fast and a powerful tier can speak different wire protocols and have
        different context windows). Provider-independent inputs (profile/pins/
        restrict_to/fixed_cost/max_tools) are captured from the enclosing scope; the
        window is resolved per provider (REACT-1/F032+F090: a hit on the stamped
        state.model_window on the steady path, a bounded safe-defaulted probe otherwise).
        """
        # Per-model context budget: size the presented set to the model's real window
        # so a weak/small-window model is not drowned in tool schemas.
        _window = await _resolve_execute_window(state, prov)
        if restrict_to is not None:
            schemas = tool_registry.to_provider_schema(
                prov.protocol, profile=profile, pins=pins, restrict_to=restrict_to
            )
        else:
            schemas = tool_registry.to_provider_schema(
                prov.protocol, profile=profile, pins=pins,
                request_text=state.input_text,
                budget={
                    "window": _window,
                    "fixed_cost_tokens": _fixed_cost,
                    "max_tools": _max_tools,
                },
            )
        # E8-S0 — child-toolset exclusion (PRIMARY fork-bomb cap): a delegated child
        # (delegation_depth>0) may not itself spawn/delegate, so remove those tools
        # from the PRESENTED set. Excluded by NAME defensively.
        if state.delegation_depth > 0:
            schemas = _exclude_spawn_tools(schemas)
            log.engine.debug(
                "[pipeline] execute: depth>0 — excluding spawn/delegate tools",
                extra={"_fields": {
                    "trace_id": state.trace_id,
                    "delegation_depth": state.delegation_depth,
                    "tools": len(schemas),
                }},
            )
        return schemas

    # Build the schemas for the selected (ceiling/pinned) provider — used directly on
    # the pinned/durable path and as the gateway's floor-tier seed (it rebuilds per tier).
    _window = await _resolve_execute_window(state, provider)
    tool_schemas = await build_tool_schemas(provider)
    _tools_tokens = sum(_est_tokens(json.dumps(s)) for s in tool_schemas)
    log.engine.info(
        "[pipeline] execute: context budget",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "intent_class": state.intent_class,
            "tools_used": True,
            "model_window": _window,
            "response_reserve": RESPONSE_RESERVE_TOKENS,
            "system_prompt_tokens": _est_tokens(state.system_prompt),
            "history_tokens": sum(_est_tokens(getattr(m, "content", "")) for m in state.history),
            "tools_count": len(tool_schemas),
            "tools_tokens": _tools_tokens,
            "total_est_tokens": _fixed_cost + _tools_tokens,
        }},
    )
    log.engine.info(
        "[pipeline] execute: tool_loop entry",
        extra={"_fields": {
            "trace_id": state.trace_id, "owl": state.owl_name, "tools": len(tool_schemas),
            "pinned": choice.pinned, "ceiling_tier": choice.ceiling_tier,
        }},
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
    # B4a — tool names already retried once this turn for an UNVERIFIED EFFECT
    # (success=True but verified=False). The recovery ladder's first rung re-runs a
    # non-consequential effectful tool once before routing to substitution / the
    # honest floor; a second unverified effect from the same tool falls straight
    # through (bounded — never a retry spiral).
    retried_unverified: set[str] = set()
    # ADR-5 MOVE 3 (F-26/43/72) — ephemeral, turn-scoped "approaches that already failed
    # THIS turn" set. Keyed by ``_approach_signature`` (tool name + exact inputs) so a
    # blind re-issue of the SAME approach is recognised and steered away instead of
    # re-executed. NEVER persisted (positive-only directive honoured — this is pure
    # within-turn awareness, gone at turn end). Distinct from the by-name circuit breaker
    # (finer: exact args; fires on the FIRST repeat, not after a threshold). Flag-gated:
    # ``trustworthy_learning`` OFF ⇒ the set is never consulted/filled ⇒ byte-identical.
    failed_approaches: set[str] = set()
    _trustworthy_learning = False
    try:
        from stackowl.config.settings import Settings

        _trustworthy_learning = bool(Settings().trustworthy_learning)
    except Exception:  # noqa: BLE001 — a flag read must never break dispatch
        _trustworthy_learning = False
    # TurnProgressTracker — unified replacement for the P2 fail_streak/circuit_open
    # pair. Closes G1 (timeout) and G2 (no-op refusal) spiral gaps in addition to
    # the original same-tool repeated-failure containment. Window-scaled threshold:
    # lean models get a tighter cap (2 vs 3) so they're contained faster.
    from stackowl.pipeline.progress_tracker import TurnProgressTracker, resolve_no_progress_threshold
    _np_threshold = resolve_no_progress_threshold(state.model_window)
    progress = TurnProgressTracker(threshold=_np_threshold)

    def _stamp_progress(st: PipelineState) -> PipelineState:
        """Stamp turn-progress summary onto state at every _run_with_tools exit."""
        return st.evolve(
            turn_made_progress=progress.made_progress,
            no_progress_tools=progress.opened_tools,
        )

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
        # Incident P2 — circuit-open bounce. A tool that failed
        # SAME_TOOL_FAILURE_THRESHOLD times in a row this turn is unavailable for
        # the rest of the turn. This is a PRE-EXECUTION REFUSAL (like
        # denied_this_run): it records NOTHING in the outcome ledger, so it cannot
        # trip the consequential give-up floor (P0 honesty invariant). Steer the
        # model to change approach or stop; the string carries no case-specifics.
        if progress.is_open(name):
            log.engine.warning(
                "[pipeline] execute: circuit open — tool bounced for remainder of turn",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                   "threshold": _np_threshold}},
            )
            # PA3 — a dead-ended breaker used to leave the model stuck on the
            # weak tier. Feed the open event INTO the existing model-tier ladder:
            # request escalation so the provider loop returns ESCALATE_SENTINEL and
            # the gateway re-runs one tier up. Containment is PRESERVED — the
            # refusal string still returns, so THIS tier never re-offers the dead
            # tool. At the ceiling (can_escalate False) the request is ignored and
            # the existing honest floor takes over.
            request_escalation(name)
            log.engine.info(
                "[pipeline] execute: circuit open — requested tier escalation",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
            )
            return _circuit_open_refusal(name)
        # ADR-5 MOVE 3 (F-26/43/72) — within-turn failed-approach consult. When the model
        # blindly RE-ISSUES the EXACT approach (this tool + these same inputs) that already
        # failed earlier this turn, do not re-execute it: steer to a different approach.
        # This fires BEFORE the recovery ladder's internal retries (those re-enter
        # ``_guarded_dispatch``, not ``_dispatch``), so a transient that self-heals is never
        # blocked — only a model-issued blind repeat is. PRE-EXECUTION containment: records
        # nothing, carries no TOOL_FAILED_MARKER (P0 honesty). Flag OFF ⇒ set is empty here
        # ⇒ this branch is dead ⇒ byte-identical.
        _approach_sig = _approach_signature(name, args) if _trustworthy_learning else ""
        if _approach_sig and _approach_sig in failed_approaches:
            log.engine.info(
                "[pipeline] execute: within-turn approach already failed — steering to change",
                extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
            )
            return _repeated_approach_refusal(name)
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
            except Exception as exc:  # noqa: BLE001 — provenance is best-effort, never fatal
                log.engine.debug(
                    "[pipeline] execute: deny-provenance recompute failed",
                    exc_info=exc,
                    extra={"_fields": {
                        "tool": name, "owl": state.owl_name, "trace_id": state.trace_id,
                    }},
                )
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
        # REACT-5/F061 — STOP PRE-CHECK: if a /stop is already pending for this turn,
        # do NOT start a fresh tool. The stop is honored at the next iteration
        # boundary; starting a (possibly long) tool first would only add latency.
        # NON-consequential read short-circuits (the durable ledger isn't touched, so
        # no exactly-once concern); a consequential/write tool is left to run so an
        # in-flight effect is not abandoned half-way (exactly-once integrity > a few
        # hundred ms of stop latency). Fail-safe: any registry miss → proceed.
        _reg = get_services().turn_registry
        if _reg is not None and t.manifest.action_severity != "consequential":
            _turn = _reg.get(state.trace_id)
            if _turn is not None and _turn.stop_requested:
                log.engine.info(
                    "[pipeline] execute: stop pending — short-circuiting tool before start",
                    extra={"_fields": {"tool": name, "trace_id": state.trace_id}},
                )
                tool_outcome_ledger.record_tool_outcome(
                    name=name, action_severity=t.manifest.action_severity, success=False,
                    # The tool never started — no side effect was attempted, so this
                    # must not count as an unachieved consequential give-up.
                    side_effect_committed=False,
                    effect_class=t.manifest.effect_class,  # TS3 — durable-effect class
                )
                return f"{TOOL_FAILED_MARKER}Not run — the turn is stopping at your request."

        # L3 — REQUIRED-PARAMETER PRE-VALIDATION. A weak model can emit a tool call
        # that omits a required arg (the live incident: `memory` called with an empty
        # `action`). Refuse it BEFORE execute so a malformed no-op never reaches the
        # tool body, and record it as a NO-side-effect refusal so it cannot trip the
        # give-up floor (composes with L1). Hand the model a crisp, self-correcting
        # message naming the missing parameter. Fail-safe: any non-list schema → skip.
        _schema = t.manifest.parameters if isinstance(t.manifest.parameters, dict) else {}
        _required = _schema.get("required")
        if isinstance(_required, list) and _required and isinstance(args, dict):
            # A required param is "unset" only when ABSENT or explicitly null. An
            # empty string is NOT unset — several write tools legitimately take one
            # (write_file content="" creates an empty file; edit new_string="" is a
            # deletion). A semantically-bad blank is the tool's OWN concern: its
            # validation returns success=False with side_effect_committed=False (L1),
            # which already avoids the floor without us blocking the call here.
            def _is_unset(key: object) -> bool:
                return key not in args or args[key] is None

            _missing = [p for p in _required if _is_unset(p)]
            if _missing:
                log.engine.info(
                    "[pipeline] execute: tool call missing required parameter(s) — refusing pre-execute",
                    extra={"_fields": {
                        "tool": name, "missing": _missing, "trace_id": state.trace_id,
                    }},
                )
                tool_outcome_ledger.record_tool_outcome(
                    name=name, action_severity=t.manifest.action_severity, success=False,
                    side_effect_committed=False,
                    effect_class=t.manifest.effect_class,  # TS3 — durable-effect class
                )
                # G2 — a missing-param refusal is zero-progress: advance the streak so
                # a weak model that omits a required arg on every call gets bounced.
                progress.record_no_progress(name)
                _req = ", ".join(str(p) for p in _required)
                return (
                    f"{TOOL_FAILED_MARKER}The call to '{name}' is missing required "
                    f"parameter(s): {', '.join(str(p) for p in _missing)}. "
                    f"Required: {_req}. Re-issue the call with every required parameter set."
                )

        # S2 durable-react — route the real tool call through the exactly-once
        # ledger guard. DORMANT: with no active DurableReActContext (every path
        # today) this is a transparent `await t(**args)`. Only a side-effecting
        # tool under an active durable task is ledger-guarded (exactly-once).
        # REACT-5/F061 — bound the tool's OWN awaitable: asyncio.wait_for cancels the
        # tool coroutine (not the turn task) at the per-tool deadline so a hung/long
        # tool can never block the loop — or a co-pending stop — indefinitely.
        from stackowl.learning.heuristic_matcher import match_and_log
        from stackowl.pipeline.durable.ledger_guard import ledger_guard

        async def _guarded_dispatch(d_args: dict[str, object]) -> ToolResult | None:
            """Run ``t`` once through the exactly-once ledger guard + per-tool
            deadline, record its outcome (ledger + progress tracker), and return the
            ToolResult. Returns ``None`` iff the tool exceeded its deadline (the
            caller renders the timeout marker). Shared by the initial dispatch and
            the B4a unverified-effect retry so both record IDENTICALLY — the retry is
            a real second attempt with its own ledger outcome, not a silent re-run."""
            try:
                r = await asyncio.wait_for(
                    ledger_guard(name, d_args, t.manifest.action_severity, lambda: t(**d_args)),
                    timeout=_TOOL_DEADLINE_S,
                )
            except TimeoutError:
                log.engine.warning(
                    "[pipeline] execute: tool exceeded per-tool deadline — cancelled",
                    extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                       "deadline_s": _TOOL_DEADLINE_S}},
                )
                tool_outcome_ledger.record_tool_outcome(
                    name=name, action_severity=t.manifest.action_severity, success=False,
                    effect_class=t.manifest.effect_class,  # TS3 — durable-effect class
                )
                # G1 — a timeout is zero-progress: advance the streak so a tool that
                # keeps timing out gets bounced rather than spiralling the budget.
                progress.record_no_progress(name)
                return None
            tool_outcome_ledger.record_tool_outcome(
                name=name, action_severity=t.manifest.action_severity, success=r.success,
                # L1 — a tool that pre-execution-refuses (bad args, unavailable store)
                # reports side_effect_committed=False so a no-op failure does not trip
                # the honest give-up floor as if a real consequential action had failed.
                side_effect_committed=r.side_effect_committed,
                # B2 — the reality check: an effectful tool that claimed success but
                # whose artifact was not observed (verified=False) is recorded as an
                # unachieved outcome, so the honest floor owns the turn. None ⇒
                # byte-identical.
                verified=r.verified,
                effect_class=t.manifest.effect_class,  # TS3 — durable-effect class
            )
            # TurnProgressTracker — update from this REAL completed dispatch. A
            # TRUSTWORTHY success resets the streak; ANY non-trustworthy result (a
            # genuine failure OR an unverified effect, regardless of
            # side_effect_committed) is zero-progress. B4a: keying this on
            # is_trustworthy_success (not raw success) means a claimed-but-unobserved
            # effect can no longer disarm the same-tool circuit breaker.
            if is_trustworthy_success(r.success, r.verified):
                progress.record_progress(name)
            else:
                # ADR-5 MOVE 3 — remember this EXACT approach failed this turn so a later
                # blind re-issue is steered away (consulted at the top of ``_dispatch``).
                # Ephemeral, never persisted; ``_approach_sig`` is "" when the flag is OFF.
                if _approach_sig:
                    failed_approaches.add(_approach_sig)
                opened = progress.record_no_progress(name)
                if opened:
                    log.engine.warning(
                        "[pipeline] execute: same-tool failure threshold reached — circuit open",
                        extra={"_fields": {"tool": name, "trace_id": state.trace_id,
                                           "threshold": _np_threshold}},
                    )
            # F038 — honest no-IO log of the tool outcome (the old per-call heuristic
            # matcher had no production subscriber). match_and_log never raises.
            match_and_log(tool_name=name, tool_result=r)
            return r

        tr = await _guarded_dispatch(args)
        if tr is None:
            return (
                f"{TOOL_FAILED_MARKER}The action '{name}' was cancelled after exceeding the "
                f"{_TOOL_DEADLINE_S:.0f}s per-tool time limit and did not complete."
            )
        # B4a — the dispatch returns a win ONLY for a TRUSTWORTHY success. A
        # claimed-but-unobserved effect (success=True, verified=False) no longer
        # returns its (misleading) "done!" output here; it enters the recovery ladder.
        if is_trustworthy_success(tr.success, tr.verified):
            # F-4 (DEFERRED — intentionally NOT a decision-time learned-heuristic
            # consult here). A per-call heuristic-store DB lookup on this hot path
            # was deliberately removed (latency win; it fed no consumer) and is
            # guarded by tests/learning/test_heuristic_demote.py
            # ::test_execute_does_not_do_a_per_call_heuristic_db_lookup. Re-surfacing
            # learned hints to the model needs its own design — weak-model
            # amplification safety + a real consumer — per the note in
            # learning/heuristic_matcher.py. Do not add a heuristic store read here.
            return tr.output
        # B4a recovery ladder — RUNG 1: RETRY-ONCE on a recoverable failure. Two
        # recoverable shapes self-heal on a second attempt and so earn one retry:
        #   (1) an UNVERIFIED EFFECT — a non-consequential effectful tool that CLAIMED
        #       success but whose result was not observed (success=True, verified=False).
        #       A transient miss (slow flush / race) self-heals; a genuine no-op (the
        #       disguised --simulate class) fails identically and falls through.
        #   (2) F-7 — a TRANSIENT GENUINE FAILURE (success=False) whose error looks
        #       like an infrastructure fault (dropped connection, reset socket, locked
        #       DB, closed pipe) per the shared dead-handle vocabulary. A deterministic
        #       failure (bad input, refusal, missing capability) is NOT retried — it
        #       would fail identically — and drops straight to substitution / the floor.
        # Bounded to one retry per tool per turn (the shared retried_unverified set) —
        # never a retry spiral. CONSEQUENTIAL tools are NEVER auto-retried: an
        # irreversible effect must not be re-fired blind.
        # ADR-2 — DELEGATE the retry DECISION to the one RecoveryActuator. classify_tool_failure
        # derives the same two recoverable shapes (unverified_effect = success∧verified is False;
        # transient = ¬success∧dead-handle-marker) and should_retry applies the same
        # not-consequential guard — byte-identical to the former inline predicate, but the policy
        # now lives with the authority (the execute loop is its tool-dispatch caller). The
        # one-retry-per-turn bound (retried_unverified) stays here (it is loop state, not policy).
        from stackowl.pipeline.recovery_actuator import (
            RecoveryActuator,
            classify_tool_failure,
        )

        _failure = classify_tool_failure(tr, name=name, consequential=is_consequential)
        if RecoveryActuator().should_retry(_failure) and name not in retried_unverified:
            retried_unverified.add(name)
            log.engine.info(
                "[pipeline] execute: recoverable failure — retrying once (recovery rung 1)",
                extra={"_fields": {
                    "tool": name, "trace_id": state.trace_id,
                    "reason": "unverified_effect" if _failure.unverified_effect else "transient_failure",
                }},
            )
            retry_tr = await _guarded_dispatch(args)
            if retry_tr is not None and is_trustworthy_success(retry_tr.success, retry_tr.verified):
                # The retry observed the effect — record a (bridging) recovery so the
                # honest floor knows the consequential goal WAS achieved on attempt 2.
                recovery_context.record_recovery(
                    kind="retry", failed=name, recovered_via=name, user_visible=False,
                )
                return retry_tr.output
            if retry_tr is not None:
                tr = retry_tr  # carry the freshest result forward for the floor marker
        # RUNG 2 — W3.T14 substitution: route around the broken capability via an
        # in-bounds, NON-consequential sibling sharing the capability_tag, run through
        # the SAME guarded path. CONSENT-SAFE (find_substitute excludes consequential
        # siblings) + BOUNDS-SAFE (same check_effective_bounds verdict). One
        # substitution per capability per turn. Any actuator error → fall through.
        sub = await _try_substitute(
            failed_tool=name,
            failed_args=args,
            tool_registry=tool_registry,
            effective=effective,
            substituted_tags=substituted_tags,
            trace_id=state.trace_id,
            locale=state.language,
        )
        if sub is not None:
            return sub
        # RUNG 3 — honest surrender. An UNVERIFIED EFFECT gets an HONEST message (NOT
        # the tool's own misleading "done!" output, which would re-introduce the very
        # false-success this arc exists to kill); a genuine failure renders its error.
        # Both carry the structural marker so the give-up judge reads them as failures.
        if tr.success and tr.verified is False:
            return (
                f"{TOOL_FAILED_MARKER}The action '{name}' reported success but its expected "
                f"result could not be confirmed (the produced artifact was not observed). "
                f"Treat it as NOT done — try another approach, or tell the user it could not "
                f"be completed."
            )
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
    # No caps / unbounded owl → default backstop applied (see _default_backstop below).
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

    _has_explicit_caps = any(
        c is not None for c in (_caps.max_steps, _caps.max_time_s, _caps.max_cost_usd)
    )
    # Default safety backstop: when the owl set NO explicit caps, apply a default
    # time/step bound so the (already-tested) BudgetGovernor always runs and every
    # turn terminates in bounded time even when a weak model spirals. NON-interactive
    # (just stop + deliver — no "Raise?" prompt; that UX is for explicit owl caps).
    _default_backstop = not _has_explicit_caps
    if _default_backstop:
        # No per-turn TIME cap — a slow (but correct) model on a remote server must
        # be allowed to finish; the wall-clock timeout was killing good turns
        # mid-work. The step backstop still prevents a genuine infinite loop; time
        # is bounded only if an owl sets an explicit max_time_s cap.
        _caps = _caps.model_copy(update={
            "max_steps": DEFAULT_TURN_MAX_STEPS,
        })
    # F093 — cumulative cost across durable resume: seed the governor with the
    # spend already accumulated by PRIOR attempts of this durable task (the
    # in-memory cost ledger resets on resume). Read off the task row; 0.0 for an
    # ephemeral turn, a first attempt, or any read failure (best-effort, never
    # blocks the turn). A negative/missing value floors at 0.0 in the governor.
    _prior_cost_usd = 0.0
    if state.task_id is not None and _services.db_pool is not None:
        try:
            from stackowl.pipeline.durable.store import DurableTaskStore
            from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

            _cost_store = DurableTaskStore(
                _services.db_pool, state.durable_owner_id or DEFAULT_PRINCIPAL_ID
            )
            _prior_cost_usd = await _cost_store.get_accumulated_cost(state.task_id)
        except Exception as exc:  # noqa: BLE001 — best-effort seed; never block the turn
            log.tasks.error(
                "[tasks] execute: prior accumulated-cost read failed — seeding 0.0",
                exc_info=exc,
                extra={"_fields": {"task_id": state.task_id, "trace_id": state.trace_id}},
            )
    _governor = BudgetGovernor(
        _caps, cost_tracker=_services.cost_tracker, trace_id=state.trace_id,
        started_monotonic=time.monotonic(), clock=_MonotonicClock(),
        prior_cost_usd=_prior_cost_usd,
        # Exclude time blocked waiting for a human clarify answer from the time cap.
        human_wait_source=current_human_wait_seconds,
    )
    # STEER-7/F094 — the clarify Raise/Stop wait scales PER CHANNEL from settings
    # (120s fallback) so a slow mobile user isn't auto-Stopped before answering.
    _clarify_wait_s = resolve_clarify_wait_timeout(state.channel, _services.settings)
    _budget_cb = make_budget_callback(
        _governor,
        interactive=(state.interactive and not _default_backstop),
        clarify=_services.clarify_gateway,
        session_id=state.session_id, channel=state.channel,
        wait_timeout_s=_clarify_wait_s,
    )

    # Task 10 — steering closure: drain THIS turn's mailbox at each iteration
    # boundary and fold a coalesced [steering] user message into the loop. Reaches
    # its own turn via state.trace_id (== the turn's request_id) → the
    # process-wide TurnRegistry on services. Fail-safe: no registry / no turn /
    # empty mailbox → returns None (loop proceeds normally).
    #
    # LOST-STEER GUARD — where it actually lives: the SOLE completion-seam guard is
    # the orchestrator's `_drain_next`, which calls `turn_registry.finalize_and_drain`
    # (flip RUNNING→FINALIZING + drain+re-route survivors) BEFORE `deregister`, so a
    # steer racing the turn's end is either converted-to-queued-new (a concurrent
    # try_steer reads FINALIZING) or drained as a survivor — never lost. PER-ITERATION
    # in-loop steering IS delivered here by `make_steering_callback` (folds THIS turn's
    # mailbox at each ReAct boundary). There is NO finalize-side in-loop re-check in
    # this function: execute.py does NOT own the ReAct loop (the provider's
    # `complete_with_tools` drives every iteration internally behind a single `await`),
    # so there is no per-iteration terminal boundary here to guard. The earlier
    # redundant finalize-side CAS primitives (`finalize_if_drained`/`drain_survivors`)
    # were dead — never wired to any caller — and were REMOVED (F051); the window they
    # targeted is closed by `finalize_and_drain`. End-to-end fold coverage:
    # tests/pipeline/test_steering_fold_end_to_end.py; completion-window property:
    # tests/gateway/test_completion_finalize_drain.py.
    _steering_cb = make_steering_callback(_services.turn_registry, state.trace_id)

    # Live-progress: observe-only callback (always returns None) that emits a
    # friendly "what I'm doing now" status per ReAct iteration. None when this
    # turn is gated (non-interactive / delegated child / deferred / flag off) →
    # composed list and provider call stay byte-identical to the baseline.
    _progress_cb = make_progress_callback(state, _services)

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
    # persistence_check now covers ALL turns (no interactive/depth gate — see
    # build_persistence_check's docstring); it is forwarded whenever it is non-None.
    # Omitting the kwarg when None keeps the call backward-compatible with every
    # provider implementation (no new kwarg on providers that don't accept it).
    #
    # B2 durable-react — when state.task_id is set this turn belongs to a durable
    # task: activate a DurableReActContext for the drive so the (dormant) S2
    # ledger_guard becomes live (side-effecting tools → exactly-once) and pass the
    # per-iteration checkpoint callback so each ReAct round is persisted. When
    # task_id is None (every non-durable turn) NONE of this runs and the call is
    # made EXACTLY as before (no context, no extra kwargs) — byte-for-byte.
    async def _on_tier_escalate(from_tier: str, to_tier: str) -> None:
        reset_ledger_for_tier_escalation(from_tier, to_tier, trace_id=state.trace_id)
        # PA3 — clear the escalation request and re-arm the breaker so the fresh,
        # stronger tier starts clean (it is not pre-bounced by the weak tier's
        # open breaker, and won't immediately re-escalate off a stale flag).
        clear_escalation()
        progress.reset()

    async def _call_default() -> tuple[str, list[dict[str, Any]]]:
        _extra: dict[str, Any] = {}
        if persistence_check is not None:
            _extra["persistence_check"] = persistence_check
        # Budget gate first (it may Raise to stop the loop), then steering fold,
        # then progress LAST (observe-only — must run after the short-circuiting
        # callbacks so a budget Raise pre-empts a pointless progress emit).
        _default_cb = _compose_iter_cbs(
            [c for c in (_budget_cb, _steering_cb, _progress_cb) if c is not None]
        )
        if _default_cb is not None:
            _extra["on_iteration_complete"] = _default_cb
        # PINNED choice (owl-named provider / manifest pin / explicit session tier) →
        # honour it EXACTLY: call the resolved provider directly, no escalation. F027 —
        # the execute step owns the BudgetGovernor; compute the residual wall-clock
        # budget HERE and thread it as wrapup_deadline_s (the provider gets a VALUE).
        # (A None registry — defensive; run() already gated on it — also takes the
        # direct path since the gateway needs the registry to resolve tiers.)
        _preg = _services.provider_registry
        if choice.pinned or _preg is None:
            return await provider.complete_with_tools(
                user_text=state.input_text,
                system_text=state.system_prompt,
                tool_schemas=tool_schemas,
                tool_dispatcher=_dispatch,
                history=list(state.history),
                wrapup_deadline_s=_governor.remaining_seconds(),
                **_extra,
            )
        # NON-PINNED → start at "fast" and escalate fast→…→ceiling through the gateway.
        # It rebuilds schemas per tier (build_tool_schemas), recomputes the residual
        # wrap-up budget per attempt (wrapup_deadline_fn), passes can_escalate below the
        # ceiling so a persistent leak/give-up returns the ESCALATE sentinel (re-run on
        # a stronger tier) instead of leaking raw text, and resets the ledger between
        # discarded attempts (on_escalate). If even the top tier fails the loop returns
        # an honest floor (never raw JSON) which flows through the same surfaces below.
        from stackowl.providers.llm_gateway import LLMGateway

        gateway = LLMGateway(_preg)
        return await gateway.complete_with_tools(
            user_text=state.input_text,
            system_text=state.system_prompt,
            tool_schemas=tool_schemas,
            tool_dispatcher=_dispatch,
            floor=choice.floor_tier,
            ceiling=choice.ceiling_tier,
            purpose="execute.tool_loop",
            build_tool_schemas=build_tool_schemas,
            wrapup_deadline_fn=_governor.remaining_seconds,
            on_escalate=_on_tier_escalate,
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

        # F093 — persist the CUMULATIVE cost (prior attempts + this attempt) on the
        # task row each completed iteration so the next resume seeds its governor
        # with it and the cost ceiling holds across the whole task. Writes the
        # governor's ABSOLUTE current cumulative spend (monotonic, idempotent on
        # replay — never an additive delta). Best-effort: a persist failure is
        # logged and swallowed so cost bookkeeping never breaks a durable drive.
        async def _persist_cost_cb(
            _s: ReActIterationState,
        ) -> list[dict[str, Any]] | None:
            try:
                await session.store.set_accumulated_cost(
                    task_id, _governor.current_cost_usd()
                )
            except Exception as exc:  # noqa: BLE001 — never break the drive on cost I/O
                log.tasks.error(
                    "[tasks] execute: durable cost persist failed — continuing",
                    exc_info=exc,
                    extra={"_fields": {"task_id": task_id, "trace_id": state.trace_id}},
                )
            return None

        # E2-S4 / Task 10 — compose in order: checkpoint the completed iteration
        # first (so a breached turn is still durably recorded and the resume seam
        # can replay from it on a Raise), THEN gate budget, THEN fold steering.
        # Checkpoint + budget return None (no fold); steering returns the
        # [steering] message. _compose_iter_cbs concatenates any folded messages
        # (Task 9 splice contract) so no callback's splice is silently lost.
        _iter_cb = _compose_iter_cbs(
            [c for c in (cb, _persist_cost_cb, _budget_cb, _steering_cb, _progress_cb)
             if c is not None]
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
                wrapup_deadline_s=_governor.remaining_seconds(),  # F027 — bound the wrap-up
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

    # Surface "Working on it…" the instant the loop begins so the user sees life
    # within ~1s (best-effort; no-op when progress is gated).
    await emit_progress_start(_progress_cb)

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
        return _stamp_progress(state.evolve(
            durable_parked=True,
            errors=(*state.errors, marker),
        ))
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
                               "tool_calls": len(exc.tool_call_records),
                               "drained_steers": len(exc.drained_steers)}},
        )
        # REACT-6/F033 — a steer co-arriving with the stop was drained from the
        # mailbox by the boundary callback but could not be folded into a stopping
        # turn. Re-route it as a queued-new turn so the user's message is preserved
        # rather than silently lost. Fail-safe: never breaks the stop finalize.
        if exc.drained_steers and _services.turn_registry is not None:
            try:
                await _services.turn_registry.requeue_steers_as_new(
                    exc.request_id, exc.drained_steers
                )
            except Exception as _req_exc:  # never let re-route break the clean stop
                log.engine.error(
                    "[pipeline] execute: re-routing stopped-turn steers failed",
                    exc_info=_req_exc,
                    extra={"_fields": {"trace_id": state.trace_id,
                                       "request_id": exc.request_id,
                                       "drained_steers": len(exc.drained_steers)}},
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
        return _stamp_progress(state.evolve(
            responses=(*state.responses, *_stopped_chunks),
            tool_calls=(*state.tool_calls, *_stopped_tool_records),
            errors=(*state.errors, f"turn:stopped:{exc.request_id}"),
        ))
    except BudgetBreach as exc:
        log.engine.info(
            "[pipeline] execute: budget cap reached — stopping with partial",
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name,
                               "cap": exc.cap, "limit": exc.limit, "actual": exc.actual}},
        )
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
        if _default_backstop:
            # Default safety backstop: deliver clean best-available partial with no
            # developer-facing budget marker in the content.  If partial is empty,
            # route to the synthesize_floor path (same never-empty guarantee as the
            # general exception handler).
            if exc.partial_text:
                _breach_chunks = (ResponseChunk(
                    content=exc.partial_text, is_final=False, chunk_index=0,
                    trace_id=state.trace_id, owl_name=state.owl_name,
                ),)
                # D2 — stamp the consequential snapshot on the budget-cap return so the
                # terminal honest floor decides on IMMUTABLE state (the ledger ContextVar
                # may be torn down by the time the floor runs — F099). budget_capped=True
                # arms the floor's goal-relevant (delivered-only) accounting (D1).
                return _stamp_progress(_snapshot_consequential(state).evolve(
                    responses=(*state.responses, *_breach_chunks),
                    tool_calls=(*state.tool_calls, *_breach_tool_records),
                    errors=(*state.errors, marker),
                    budget_capped=True,
                ))
            # Empty partial under the default backstop → graceful slot-free floor
            # (no raw budget error / blank capability fields surfaced to the user).
            floor = synthesize_floor(
                goal=state.input_text,
                error=None,
                attempts=[],
                partial=None,
            )
            floor_chunk = ResponseChunk(
                content=floor,
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
                is_floor=True,
            )
            return _stamp_progress(_snapshot_consequential(state).evolve(
                responses=(*state.responses, floor_chunk),
                tool_calls=(*state.tool_calls, *_breach_tool_records),
                errors=(*state.errors, marker),
                budget_capped=True,
            ))
        # Explicit cap: deliver partial with a human-visible budget note.
        note = f"\n\n[stopped: budget cap '{exc.cap}' reached (limit {exc.limit}, used {exc.actual})]"
        _stop_content = (exc.partial_text + note) if exc.partial_text else note
        _breach_chunks = (ResponseChunk(
            content=_stop_content, is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        ),)
        return _stamp_progress(_snapshot_consequential(state).evolve(
            responses=(*state.responses, *_breach_chunks),
            tool_calls=(*state.tool_calls, *_breach_tool_records),
            errors=(*state.errors, marker),
            budget_capped=True,
        ))
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
        return _stamp_progress(state.evolve(
            responses=(*state.responses, floor_chunk),
            errors=(*state.errors, format_step_error("execute", exc)),
            step_errors=(*state.step_errors,
                         StepError(step="execute", exc_type=type(exc).__name__, message=str(exc))),
        ))

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
    return _stamp_progress(state.evolve(
        responses=(*state.responses, *chunks),
        tool_calls=(*state.tool_calls, *tool_records),
    ))


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


def _clarify_resolvable_from_context(state: PipelineState) -> bool:
    """F-3 — True when an interactive clarify question can be RESOLVED from the
    turn's available context, so the turn should ACT (fall through to the tool
    path) instead of surfacing the question.

    Act-first: the router emits a clarify verdict only for an ambiguous action it
    judged consequential/irreversible (its prompt gates clarify on commitment
    cost). But re-asking a question the user has ALREADY been shown this turn is an
    unproductive loop, not a genuine ambiguity — so when the SAME question already
    appears in the turn's prior context (recalled durable memory or the
    conversation history threaded onto the state), resolve it by acting rather than
    asking again. A first-time, genuinely-unresolved question still surfaces.

    Deterministic + side-effect-free (no I/O, no LLM, no keyword list — it matches
    the router-authored question text against context the turn already carries).

    DEFERRED (architectural / out of this finding's safe subset): LLM-assisted
    resolution of a never-before-seen question from durable memory, and a
    structured consequential/irreversible signal threaded from the router so the
    execute layer can re-apply the commitment-cost gate independently (today that
    gate lives in the router prompt).
    """
    question = (state.clarify_question or "").strip()
    if not question:
        return False
    haystacks: list[str] = []
    if state.memory_context:
        haystacks.append(state.memory_context)
    for msg in state.history:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content:
            haystacks.append(content)
    return any(question in h for h in haystacks)


async def _maybe_clarify(state: PipelineState, services: object) -> PipelineState | None:
    """If this is an INTERACTIVE clarify turn, surface ONE question and yield.

    Registers a turn-yield pending clarify (deliver=False — the question is the
    streamed response, so a second send_clarify would double-deliver) and returns
    a state whose single response IS the question.  Returns None when this is not
    a clarify turn OR there is no human to answer (cron/parliament) — the caller
    then proceeds to the standard tool path (best-effort action).

    4-point log: entry / decision / step / exit.
    """
    # 1. ENTRY
    log.engine.debug(
        "[pipeline] _maybe_clarify: entry",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "intent_class": state.intent_class,
            "interactive": state.interactive,
            "has_question": bool(state.clarify_question),
        }},
    )
    # 2. DECISION — only act on an interactive clarify turn with a question
    if state.intent_class != "clarify" or not state.clarify_question:
        log.engine.debug(
            "[pipeline] _maybe_clarify: not a clarify turn — passing through",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None
    if not state.interactive:
        log.engine.info(
            "[pipeline] _maybe_clarify: clarify verdict in a non-interactive context — "
            "falling through to the standard tool path",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None
    # F-3 — before surfacing, try to resolve the ambiguity from the turn's own
    # context. If it is resolvable (e.g. the same question is already in context —
    # an unproductive re-ask), ACT instead of asking: fall through to the standard
    # tool path so the assistant makes a best-effort action (act-first), exactly as
    # the non-interactive path does.
    # ADR-3: route the act-first-vs-park DECISION through the one ReversibilityResolver.
    # A clarify verdict that is resolvable from context (an unproductive re-ask) is a
    # REVERSIBLE/low-stakes decision the assistant may act on; an unresolved verdict is
    # the router's irreversible/high-commitment judgement that must reach the human.
    # ``must_reach_user`` reproduces ``not _clarify_resolvable_from_context`` exactly
    # (byte-identical). OFF ⇒ the inline check runs.
    resolvable = _clarify_resolvable_from_context(state)
    if reversibility_resolver_enabled():
        decision = Decision(
            reversibility=(
                Reversibility.reversible() if resolvable else Reversibility.irreversible()
            )
        )
        act_first = not ReversibilityResolver.must_reach_user(decision)
    else:
        act_first = resolvable
    if act_first:
        log.engine.info(
            "[pipeline] _maybe_clarify: ambiguity resolvable from context — "
            "acting instead of re-asking (act-first)",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None
    # 3. STEP — register the pending clarify (deliver=False: question IS the streamed response)
    gateway = getattr(services, "clarify_gateway", None)
    if gateway is not None:
        try:
            await gateway.ask(
                state.session_id,
                state.channel,
                state.clarify_question,
                blocking=False,
                deliver=False,
            )
            log.engine.debug(
                "[pipeline] _maybe_clarify: pending clarify registered (deliver=False)",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
        except Exception as exc:  # noqa: BLE001 — never block the turn on registration failure
            log.engine.error(
                "[pipeline] _maybe_clarify: clarify pending registration failed — "
                "still surfacing the question",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id}},
            )
    else:
        log.engine.warning(
            "[pipeline] _maybe_clarify: no clarify_gateway on services — question surfaced without registration",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
    chunk = ResponseChunk(
        content=state.clarify_question,
        is_final=False,
        chunk_index=0,
        trace_id=state.trace_id,
        owl_name=state.owl_name,
    )
    # 4. EXIT
    log.engine.info(
        "[pipeline] _maybe_clarify: clarify — surfaced one question, yielding turn (no tool loop)",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
    )
    return state.evolve(responses=(*state.responses, chunk))


async def run(state: PipelineState) -> PipelineState:
    """Stream tokens from the assigned provider and build state.responses."""
    log.engine.info(
        "[pipeline] execute: entry",
        extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
    )
    # LS4 — the feedback step already captured a reaction to the last render and
    # stamped the confirmation onto responses; that confirmation IS the turn's
    # reply, so skip the tool loop entirely (no provider call). Byte-identical to
    # every normal turn (feedback_handled default False).
    if state.feedback_handled:
        log.engine.info(
            "[pipeline] execute: feedback handled this turn — skipping tool loop",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
    services = get_services()
    registry = services.provider_registry
    tool_registry = services.tool_registry
    if registry is None:
        log.engine.warning("[pipeline] execute: no provider_registry — pass-through")
        return state

    try:
        choice = select_tool_provider_plan(registry, services, state)
        provider = choice.provider
    except AllProvidersUnavailableError as exc:
        log.engine.error(
            "[pipeline] execute: all providers unavailable — flooring",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, format_step_error("execute", exc)),
            step_errors=(*state.step_errors,
                         StepError(step="execute", exc_type=type(exc).__name__, message=str(exc))),
        )
    except ToolUseUnsupportedError as exc:
        # F120 — an agentic turn was routed to a provider that cannot act and no
        # tool-capable provider exists. Floor HONESTLY (never a silent tool-free
        # reply): record the error so the critical-failure surface delivers an
        # honest "I can't act with this model" floor.
        log.engine.error(
            "[pipeline] execute: no tool-capable provider for an agentic turn — flooring honestly",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, format_step_error("execute", exc)),
            step_errors=(*state.step_errors,
                         StepError(step="execute", exc_type=type(exc).__name__, message=str(exc))),
        )

    # Clarify branch: an interactive clarify turn surfaces ONE question and yields
    # WITHOUT entering the tool loop.  Non-clarify turns (conversational/standard)
    # are byte-identical to the previous behaviour.
    _clarify_out = await _maybe_clarify(state, services)
    if _clarify_out is not None:
        return _clarify_out

    # Tool loop path: use complete_with_tools() when tools are available AND the
    # turn is not conversational.  Conversational turns take the plain-stream path
    # with zero tools so a small/weak model cannot spiral into a tool loop.
    _use_tools = (
        state.intent_class != "conversational"
        and tool_registry is not None
        and tool_registry.all()
    )
    _sp_tokens = _est_tokens(state.system_prompt)
    _hist_tokens = sum(_est_tokens(getattr(m, "content", "")) for m in state.history)
    if not _use_tools:   # tool turns now log a truthful budget line in _run_with_tools
        log.engine.info(
            "[pipeline] execute: context budget",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "intent_class": state.intent_class,
                "tools_used": bool(_use_tools),
                "system_prompt_tokens": _sp_tokens,
                # diagnostic only — assemble folds memory_context into system_prompt; NOT added to total
                "memory_context_tokens": _est_tokens(state.memory_context),
                "history_tokens": _hist_tokens,
                "total_est_tokens": _sp_tokens + _hist_tokens,
            }},
        )
    if _use_tools and tool_registry is not None:
        out = await _run_with_tools(state, choice, tool_registry)
        # REACT-7/F099 — snapshot the consequential tally + bridged set onto state
        # HERE, while the turn-scoped ledger/recovery ContextVars are still bound
        # (the backend binds them for the whole pipeline). The honest giveup floor
        # then reads the immutable snapshot rather than an implicit bind() lifetime.
        # GUARD: the BudgetBreach terminal paths inside _run_with_tools already stamp
        # the snapshot (with budget_capped=True + delivered_successes). Re-snapshotting
        # here would clobber those fields (and is fragile if ledger binding ever moves),
        # so only snapshot when one was not already taken.
        return _snapshot_consequential(out) if not out.consequential_snapshot_taken else out

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
            errors=(*state.errors, format_step_error("execute", exc)),
            step_errors=(*state.step_errors,
                         StepError(step="execute", exc_type=type(exc).__name__, message=str(exc))),
        )
    except OwlConcurrencyError as exc:
        log.engine.warning(
            "[pipeline] execute: owl concurrency limit",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        return state.evolve(
            errors=(*state.errors, format_step_error("execute", exc)),
            step_errors=(*state.step_errors,
                         StepError(step="execute", exc_type=type(exc).__name__, message=str(exc))),
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
        return state.evolve(
            errors=(*state.errors, format_step_error("execute", exc)),
            step_errors=(*state.step_errors,
                         StepError(step="execute", exc_type=type(exc).__name__, message=str(exc))),
        )

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
