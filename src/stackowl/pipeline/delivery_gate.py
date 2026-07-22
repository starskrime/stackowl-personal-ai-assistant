"""Delivery gate cascade — the 5 honesty-critical surfacers, merged into one module
(FR-11/FR-12 Phase B — physical file merge, no logic changed).

``pipeline.backends.shared.run_delivery_gate()`` (Phase A) calls these five
surfacers in sequence, pre-delivery, in both backends:

1. ``surface_persistence_handoff`` — before floor-ing, try handing a would-give-up
   turn to a better-fit owl (capability match); deliver its answer instead.
2. ``surface_consequential_giveup_floor`` — replace a dressed-up give-up (an
   unachieved consequential action, or a no-progress spiral) with the
   deterministic honest floor.
3. ``surface_overclaim_gate`` — block a confident non-floor response that
   delivered nothing real while tools failed/bounced (including the
   retrieval-intent overclaim trigger).
4. ``surface_grounding_gate`` — anti-fabrication citation integrity: strip or
   floor URLs the turn never actually retrieved.
5. ``surface_critical_failure`` — inject a localized apology when a CRITICAL
   pipeline step failed with no usable response.

This module is a byte-identical merge of what were previously 5 separate files
(``giveup_floor.py``, ``grounding_gate.py``, ``overclaim_gate.py``,
``critical_failure.py``, ``persistence_handoff.py``) — every function body below
is unchanged from its source file. Sections are ordered so every intra-module
reference resolves to something defined earlier in the file (giveup_floor →
grounding_gate → overclaim_gate → critical_failure → persistence_handoff); the
original per-file documentation is preserved as a comment block heading each
section.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit, urlunsplit

from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.owls.base_prompt import LEAN_WINDOW_THRESHOLD
from stackowl.owls.skill_ownership import read_all_skill_ownership
from stackowl.pipeline.authz_compose import child_floor
from stackowl.pipeline.delivery_decision import DeliveryDecision
from stackowl.pipeline.persistence import is_unachieved_consequential_giveup
from stackowl.pipeline.services import StepServices, get_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.step_error import parse_step_error
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.providers.base import Message
from stackowl.providers.registry import _TIER_ORDER
from stackowl.setup.localize import localize
from stackowl.tools.agents.results import provenance_footer

# =============================================================================
# giveup_floor — surface_consequential_giveup_floor: replace a dressed-up
# give-up with an honest floor.
#
# When the turn ledger shows a consequential/write action was attempted and FAILED
# with NO consequential success (the outcome was not achieved), the model's draft
# cannot be trusted to be honest about it — so REPLACE the responses with the
# deterministic honest floor naming the failed capability. Runs pre-delivery in both
# backends, BEFORE surface_critical_failure. Judge-INDEPENDENT (reads the ledger,
# not the persistence judge). Never raises.
# =============================================================================

# Recovery kind that bridges a consequential failure via a sibling tool.
# When a substitution succeeded, the capability gap was bridged — NOT a give-up.
_BRIDGING_RECOVERY_KINDS = {"substitution"}


def _unrecovered_consequential_failures(
    state: PipelineState | None = None,
) -> set[str]:
    """Names of consequential/write tools that FAILED this turn and were NOT
    bridged by a successful substitution. Empty ⇒ every effect was achieved or
    recovered.

    REACT-7/F099 — when ``state`` carries the consequential SNAPSHOT (stamped by
    execute while the ledger was live), read it instead of the ambient ContextVars,
    so the honesty decision does not depend on the bind() lifetime spanning this
    call. Falls back to the live ledger/recovery context when no snapshot was taken
    (byte-identical to the original path)."""
    if state is not None and state.has_consequential_snapshot:
        failed = set(state.consequential_failures)
        recovered = set(state.recovered_consequential)
        return failed - recovered
    failed = {
        o.name for o in tool_outcome_ledger.get_outcomes()
        if tool_outcome_ledger.is_effectful_failure(
            o.action_severity, o.success, o.side_effect_committed, o.verified,
        )
    }
    recovered = {
        e.failed for e in recovery_context.get_recovery()
        if e.kind in _BRIDGING_RECOVERY_KINDS and e.recovered_via
    }
    return failed - recovered


def is_consequential_giveup_now(state: PipelineState | None = None) -> bool:
    """True iff a consequential/write action was attempted-and-failed with NO
    consequential success AND at least one such failure was not bridged by a
    capability substitution this turn.

    REACT-7/F099 — when ``state`` carries the consequential snapshot, the tally is
    read from immutable state (not the ambient ledger ContextVar). Falls back to the
    live ledger when no snapshot was taken. Never raises. The SINGLE source of truth
    for both the nudge veto and the terminal floor."""
    try:
        if state is not None and state.has_consequential_snapshot:
            cf = len(state.consequential_failures)
            # GOAL-RELEVANT ACCOUNTING (P0 budget-cap overclaim fix). On a turn cut off
            # by the BUDGET CAP, an incidental local-workspace FILE mutation (write_file /
            # edit / apply_patch / undo_write) is NOT the user's delivered outcome — it
            # never crossed the boundary OUT. So at the budget-cap terminal path the
            # success tally is the DELIVERED subset (every effectful success EXCEPT those
            # local file mutations — consequential sends AND boundary-crossing dispatches
            # like delegate_task / sessions_* DO count). An incidental local write alongside
            # a consequential failure no longer disarms the honest floor; a turn that
            # genuinely dispatched delegated work is NOT floored. A CLEAN model-chosen stop
            # is trusted and keeps the full effectful-success tally (byte-identical to
            # today). The shared nudge-veto predicate (is_unachieved_consequential_giveup)
            # is unchanged either way.
            cs = (
                len(state.delivered_successes)
                if state.budget_capped
                else len(state.consequential_successes)
            )
        else:
            cf, cs = tool_outcome_ledger.consequential_tally()
        if not is_unachieved_consequential_giveup(cons_failures=cf, cons_successes=cs):
            return False
        # Every failed consequential must be individually bridged — a single
        # substitution does NOT cover sibling failures (per-tool recovery check).
        return bool(_unrecovered_consequential_failures(state))
    except Exception as exc:  # never raise into the loop / delivery
        log.engine.error(
            "[giveup_floor] is_consequential_giveup_now failed",
            exc_info=exc,
        )
        return False


def _name_failed_capability(
    state: PipelineState, unrecovered: frozenset[str]
) -> str | None:
    """The first unrecovered consequential failure to name in the honest floor.

    Snapshot path: first of ``state.consequential_failures`` that is unrecovered.
    Live fallback (no snapshot): first such name in ledger order. This is the EXACT
    ``failed_name`` logic the floor used inline before PA0 — extracted so the verdict
    and its named capability are computed in one place. The live-fallback branch reads
    the ledger ContextVar; it can propagate if that read raises (the caller wraps it)."""
    log.engine.debug(
        "[giveup_floor] _name_failed_capability: entry",
        extra={"_fields": {"trace_id": state.trace_id, "snapshot": state.has_consequential_snapshot}},
    )
    if state.has_consequential_snapshot:
        name = next((n for n in state.consequential_failures if n in unrecovered), None)
    else:
        name = next(
            (o.name for o in tool_outcome_ledger.get_outcomes()
             if tool_outcome_ledger.is_effectful_failure(
                 o.action_severity, o.success, o.side_effect_committed, o.verified,
             ) and o.name in unrecovered),
            None,
        )
    log.engine.debug(
        "[giveup_floor] _name_failed_capability: exit",
        extra={"_fields": {"trace_id": state.trace_id, "failed_capability": name}},
    )
    return name


def decide_delivery(state: PipelineState) -> DeliveryDecision:
    """Resolve the ONE give-up verdict for this turn (PA0 consolidation seam).

    Computes the verdict at READ time from the existing predicates
    (``is_consequential_giveup_now`` / ``_unrecovered_consequential_failures``) — the
    single source of truth is THIS FUNCTION, not a stamped field, so the verdict always
    reflects the FINAL state passed in (e.g. ``budget_capped`` set on the terminal
    return). Byte-identical to the floor's pre-PA0 inline logic: when there is no
    give-up it short-circuits WITHOUT touching the unrecovered set (no extra live-ledger
    read on a clean turn). ``is_consequential_giveup_now`` itself never raises; the
    unrecovered/name computation on the give-up path can propagate a live-ledger read
    error, which the caller's B5 wrapper backstops.

    1. ENTRY — state in. 2. DECISION — give-up vs not. 3. STEP — name on give-up only.
    4. EXIT — the bundled verdict.
    """
    log.engine.debug(
        "[giveup_floor] decide_delivery: entry",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    if not is_consequential_giveup_now(state):
        log.engine.debug(
            "[giveup_floor] decide_delivery: exit — no give-up",
            extra={"_fields": {"trace_id": state.trace_id, "consequential_giveup": False}},
        )
        return DeliveryDecision()
    unrecovered = frozenset(_unrecovered_consequential_failures(state))
    decision = DeliveryDecision(
        consequential_giveup=True,
        unrecovered_failures=unrecovered,
        failed_capability=_name_failed_capability(state, unrecovered),
    )
    log.engine.debug(
        "[giveup_floor] decide_delivery: exit — give-up",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "failed_capability": decision.failed_capability,
        }},
    )
    return decision


def _error_for_failed_capability(state: PipelineState, failed_name: str | None) -> str | None:
    """The real ``ToolResult.error`` text for ``failed_name``, or ``None`` if
    unavailable — so the honest floor cites the ACTUAL technical detail instead
    of a blank slot. Snapshot path reads the parallel
    ``consequential_failure_errors`` tuple; live fallback reads the ledger.
    Never raises."""
    if failed_name is None:
        return None
    try:
        if state.has_consequential_snapshot:
            for name, err in zip(
                state.consequential_failures, state.consequential_failure_errors, strict=False,
            ):
                if name == failed_name and err:
                    return err
            return None
        for o in tool_outcome_ledger.get_outcomes():
            if o.name == failed_name and o.error:
                return o.error
    except Exception as exc:  # noqa: BLE001 — floor enrichment must never break the turn
        log.engine.warning(
            "[giveup_floor] _error_for_failed_capability: lookup failed — omitting",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
    return None


def _attempts_for_state(state: PipelineState) -> list[str]:
    """Names of tools this turn touched, for the floor's "What I tried" slot.
    Excludes any name BRIDGED by a substitution recovery — a recovered failure was
    not a give-up and must not be named, matching the same invariant
    ``_unrecovered_consequential_failures`` enforces for the failed-capability name.
    Snapshot path unions failed+succeeded names (order not significant — the
    template joins them); live fallback lists the ledger in call order. Never
    raises."""
    try:
        if state.has_consequential_snapshot:
            recovered = set(state.recovered_consequential)
            names: tuple[str, ...] = state.consequential_failures + state.consequential_successes
        else:
            recovered = {
                e.failed for e in recovery_context.get_recovery()
                if e.kind in _BRIDGING_RECOVERY_KINDS and e.recovered_via
            }
            names = tuple(o.name for o in tool_outcome_ledger.get_outcomes())
        return list(dict.fromkeys(n for n in names if n not in recovered))
    except Exception as exc:  # noqa: BLE001 — floor enrichment must never break the turn
        log.engine.warning(
            "[giveup_floor] _attempts_for_state: lookup failed — omitting",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return []


def _floor_chunk(state: PipelineState, failed_name: str | None) -> ResponseChunk:
    """Build an is_floor=True honest-floor ResponseChunk naming ``failed_name``.

    Pure, deterministic, no model call. Shared by the consequential-giveup path and
    the no-progress-giveup path (Task 4) so the chunk shape stays byte-identical.

    When ``state.model_window`` is set and ≤ ``LEAN_WINDOW_THRESHOLD``, passes
    ``lean=True`` to :func:`synthesize_floor` so the message includes a
    capability-honest acknowledgement that the model's limited context window may
    have contributed to the failure. On a normal or unknown window ``lean=False``
    preserves byte-identical output."""
    _lean = (
        state.model_window is not None
        and state.model_window <= LEAN_WINDOW_THRESHOLD
    )
    floor_text = synthesize_floor(
        goal=state.input_text,
        error=_error_for_failed_capability(state, failed_name),
        attempts=_attempts_for_state(state) or None,
        partial=None,
        failed_capability=failed_name,
        lang=state.language,  # F089/F098 — localize the provider-down floor
        lean=_lean,
    )
    return ResponseChunk(
        content=floor_text,
        is_final=False,
        chunk_index=0,
        trace_id=state.trace_id,
        owl_name=state.owl_name,
        # SP-1 — floor-origin marker. Lets persist (F088) skip the floor prose
        # as a promotable fact, keeps the critical-failure cascade from treating
        # this honest floor as a genuine answer, and lets the pipeline floor band
        # recognize a provider floor as replaceable (no double floor).
        is_floor=True,
    )


def is_no_progress_giveup(state: PipelineState) -> bool:
    """True iff the turn made NO forward progress, delivered nothing to the user,
    and at least one tool was bounced for no-progress — i.e. the model spiraled and
    its draft cannot be trusted. INDEPENDENT of the consequential ledger (covers the
    G2 pure-refusal shape the consequential floor misses). turn_made_progress
    defaults True, so a non-tool / progressing / conversational turn is never caught."""
    try:
        if state.turn_made_progress:
            return False
        if not state.no_progress_tools:
            return False
        if state.delivered_successes:   # something crossed the boundary OUT → not a give-up
            return False
        # Don't double-floor: if the existing responses are already a floor, no-op.
        return not any(getattr(c, "is_floor", False) for c in state.responses)
    except Exception as exc:
        log.engine.error("[giveup_floor] is_no_progress_giveup failed", exc_info=exc)
        return False


async def surface_consequential_giveup_floor(state: PipelineState) -> PipelineState:
    """Replace a dressed-up give-up draft with an honest floor.

    1. ENTRY — read the turn ledger's consequential tally.
    2. DECISION — if no unachieved consequential outcome, check no-progress path.
    3. STEP — synthesize honest floor naming the failed capability.
    4. EXIT — return evolved state with responses REPLACED.
    B5 catch: never raises; logs on failure and returns state untouched.
    """
    try:
        # 1. ENTRY
        log.engine.debug(
            "[giveup_floor] surface_consequential_giveup_floor: entry",
            extra={"_fields": {"trace_id": state.trace_id, "n_responses": len(state.responses)}},
        )
        # 2. DECISION — read the ONE consolidated verdict (PA0). decide_delivery
        # returns the snapshot-stamped decision (F099) or computes it once from the
        # same predicates — byte-identical to the previous inline re-derivation.
        decision = decide_delivery(state)
        if not decision.consequential_giveup:
            log.engine.debug(
                "[giveup_floor] surface_consequential_giveup_floor: no unachieved consequential — no-op",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            # G2 honesty gap: no consequential failure, but the turn may have spiraled
            # (same tool bounced repeatedly with no progress and nothing delivered).
            if is_no_progress_giveup(state):
                log.engine.info(
                    "[giveup_floor] no forward progress — replacing draft with honest floor",
                    extra={"_fields": {
                        "trace_id": state.trace_id,
                        "failed_capability": state.no_progress_tools[0],
                    }},
                )
                return state.evolve(responses=(_floor_chunk(state, state.no_progress_tools[0]),))
            return state
        failed_name = decision.failed_capability
        # 3. STEP — build honest floor (pure, deterministic, no model call)
        log.engine.info(
            "[giveup_floor] consequential outcome not achieved — replacing draft with honest floor",
            extra={"_fields": {"trace_id": state.trace_id, "failed_capability": failed_name}},
        )
        # 4. EXIT — REPLACE the untrusted draft; never append
        return state.evolve(responses=(_floor_chunk(state, failed_name),))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[giveup_floor] failed — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state


# =============================================================================
# grounding_gate — surface_grounding_gate: anti-fabrication citation integrity
# (ADR-T3 / TS5+TS6).
#
# The bug this kills: the model presented "AI news" with FAKE links (a made-up
# GPT-5.6 announcement, a fabricated URL) while NO web_search/web_fetch ever ran —
# pure hallucination dressed as sourced fact.
#
# MEASURED, never prose-scanned. The rule keys ONLY on URLs + the per-turn retrieval
# ledger (``state.tool_calls``), never on classifying the topic of the answer:
#
#   * FETCHED-SOURCE SET — every URL that actually came back from a ``web_search``
#     result or was successfully ``web_fetch``'d THIS turn (read from the tool
#     records). This is the only set of URLs the turn is entitled to cite.
#   * Any http(s) URL in the answer NOT in the fetched set (and not one the USER
#     themselves pasted this turn — echoing the user is not fabrication) is a
#     FABRICATED citation → stripped.
#   * TS5 retrieval gate: if the answer carries external URLs but the turn retrieved
#     ZERO sources — no retrieval tool ran, OR one ran and came back EMPTY (the
#     "empty scheduled cycle": a recurring poke whose web_search found nothing) —
#     every URL is fabricated by definition and the external claim is ungrounded →
#     floor to the honest "I didn't actually look this up", never a husk of invented
#     prose with the link stripped. Likewise if stripping guts the answer → floor.
#
# Runs pre-deliver in both backends, AFTER the give-up / overclaim floors (so it
# no-ops on an already-floored draft). Never raises. Byte-identical when the answer
# contains no URLs.
# =============================================================================

# Tools whose results contribute to the fetched-source set. Both declare
# capability_tag="web_knowledge"; keyed by name because the parse shape differs per
# tool. ponytail: extend this set if a new first-class retrieval tool lands.
_RETRIEVAL_TOOLS = frozenset({"web_search", "web_fetch"})

# Unicode-safe http(s) URL scanner. Matches the scheme + everything up to the first
# whitespace or a delimiter that cannot be part of a URL; trailing punctuation is
# trimmed separately so "(see https://x.com/a.)" yields "https://x.com/a".
_URL_RE = re.compile(r"https?://[^\s<>\)\]\"'`]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?。．)]}>\"'`*"

# A stripped answer with fewer than this many Unicode word-chars left is "gutted"
# (the URLs were carrying the substance) → floor instead of delivering a husk.
# Lowered 20 -> 5 on 2026-07-22 (owner decision: relax content-shaping gates) —
# NOT removed outright, unlike most limits in this pass: this is the one thing
# standing between a legitimately-stripped answer and delivering a literally
# blank/near-blank message with no explanation, a worse outcome than the honest
# floor it replaces. 5 still lets through anything with real remaining content.
_MIN_SUBSTANCE_WORDCHARS = 5
_WORDCHAR_RE = re.compile(r"\w", re.UNICODE)

_FLOOR_TEXT = (
    "I couldn't verify sources for this — I didn't actually retrieve it, so I "
    "can't stand behind those links. Want me to look it up properly?"
)

_SCHEDULE_FLOOR_TEXT = (
    "I said I'd do that later, but I didn't actually schedule anything — no "
    "job was set up, so it won't happen on its own. Want me to actually "
    "schedule it now?"
)


def _normalize_url(raw: str) -> str:
    """Canonicalize a URL for set membership: lowercase scheme+host, drop the
    fragment, strip a trailing path slash. Query is KEPT (a different query is a
    different page). Returns "" for an unparseable value."""
    try:
        parts = urlsplit(raw.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            return ""
        path = parts.path.rstrip("/")
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
        )
    except ValueError:
        return ""


def _extract_urls(text: str) -> list[str]:
    """Every http(s) URL in ``text`` (raw, trailing punctuation trimmed), in order."""
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(_TRAILING_PUNCT)
        if url:
            out.append(url)
    return out


def _fetched_source_set(state: PipelineState) -> set[str]:
    """Normalized URLs the turn actually retrieved: every ``url`` field returned by a
    ``web_search`` result + every successfully ``web_fetch``'d URL. Read from the
    immutable per-turn tool records — the MEASURED ledger, not the answer prose."""
    fetched: set[str] = set()
    for call in state.tool_calls:
        if call.tool_name not in _RETRIEVAL_TOOLS:
            continue
        if call.tool_name == "web_search":
            # Result is the JSON envelope: {"data": {"web": [{"url": ...}, ...]}}.
            # Pull every URL textually — robust to shape drift, never raises.
            for url in _extract_urls(call.result or ""):
                norm = _normalize_url(url)
                if norm:
                    fetched.add(norm)
        else:  # web_fetch — the fetched URL is its own source, when the fetch succeeded.
            if call.error:
                continue
            norm = _normalize_url(str(call.args.get("url", "")))
            if norm:
                fetched.add(norm)
    return fetched


def _retrieval_ran(state: PipelineState) -> bool:
    """True iff any retrieval tool was invoked this turn (success or empty)."""
    return any(c.tool_name in _RETRIEVAL_TOOLS for c in state.tool_calls)


def _answer_text(state: PipelineState) -> str:
    """Concatenated text of the durable ANSWER chunks (progress chunks excluded)."""
    return "".join(c.content for c in state.responses if c.kind == "answer")


def _strip_urls(text: str, fabricated: set[str]) -> str:
    """Remove fabricated URLs from ``text``. A markdown link ``[label](badurl)``
    collapses to ``label``; a bare URL is removed. Membership is by normalized form."""

    def _md(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        return label if _normalize_url(url) in fabricated else match.group(0)

    text = re.sub(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", _md, text)

    def _bare(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        return "" if _normalize_url(url) in fabricated else match.group(0)

    return _URL_RE.sub(_bare, text)


def _is_gutted(text: str) -> bool:
    """True if too little substance remains to stand as an answer."""
    return len(_WORDCHAR_RE.findall(text)) < _MIN_SUBSTANCE_WORDCHARS


def _grounding_floor_chunk(state: PipelineState) -> ResponseChunk:
    """Deterministic honest floor for an ungrounded external-info answer."""
    return ResponseChunk(
        content=_FLOOR_TEXT,
        is_final=False,
        chunk_index=0,
        trace_id=state.trace_id,
        owl_name=state.owl_name,
        is_floor=True,
    )


def _schedule_commit_floor_chunk(state: PipelineState) -> ResponseChunk:
    """Deterministic honest floor for a text-only future-scheduling promise."""
    return ResponseChunk(
        content=_SCHEDULE_FLOOR_TEXT,
        is_final=False,
        chunk_index=0,
        trace_id=state.trace_id,
        owl_name=state.owl_name,
        is_floor=True,
    )


async def _try_corrective_rerun(
    state: PipelineState, correction: str
) -> tuple[ResponseChunk, ...] | None:
    """One bounded agentic self-correction: re-run the pipeline with the gate's
    rejection reason fed back to the model, and adopt the corrected answer.

    Returns the corrected turn's answer chunks (re-stamped onto THIS turn's
    trace) when the corrective child cleared its own gates, else ``None`` (the
    caller keeps its honest floor). Bounded by ``state.corrective_replay`` — a
    correction is never corrected again. Skipped for write-effect culprits by
    the callers (side-effect safety mirrors delegate_task's "a write-capable
    failure is halted, not retried"). Never raises.
    """
    if state.corrective_replay:
        return None
    try:
        actuator = get_services().retry_actuator
        if actuator is None:
            return None
        corrected = await actuator.run_corrective(original=state, correction=correction)
        if corrected is None:
            return None
        # "".join — streamed child responses are one chunk per token (see
        # retry_actuator.attempt_retry's identical join and its comment).
        answer_text = "".join(c.content for c in corrected.responses if c.content).strip()
        if not answer_text:
            return None
        return (
            ResponseChunk(
                content=answer_text,
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            ),
        )
    except Exception as exc:  # no-hidden-errors: keep the floor, loudly
        log.engine.error(
            "[delivery_gate] corrective rerun failed — keeping floor",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None


async def _try_fulfill_schedule_commit(state: PipelineState) -> str | None:
    """Attempt to ACTUALLY create the promised job; receipt line or ``None``.

    Built lazily from ``services.provider_registry`` (no new wiring point);
    every degraded path — no registry, extraction failure, cronjob refusal —
    returns ``None`` so the caller falls back to the honest ask-floor. Never
    raises.
    """
    try:
        registry = get_services().provider_registry
        if registry is None:
            return None
        from stackowl.interaction.schedule_commit_fulfiller import (
            ScheduleCommitFulfiller,
        )

        return await ScheduleCommitFulfiller(registry).fulfill(
            response=_answer_text(state), request=state.input_text
        )
    except Exception as exc:  # no-hidden-errors: fall back to the floor, loudly
        log.engine.error(
            "[overclaim_gate] schedule fulfillment failed — falling back to floor",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None


async def surface_grounding_gate(state: PipelineState) -> PipelineState:
    """Strip fabricated citations / floor an ungrounded external-info answer.

    1. ENTRY — gather the answer URLs.
    2. DECISION — which URLs are fabricated (not fetched, not user-supplied).
    3. STEP — strip them, or floor if retrieval never ran / the answer is gutted.
    4. EXIT — evolved state, or the original (byte-identical) when nothing to do.
    Never raises: on any internal error the original draft is returned untouched.
    """
    try:
        # Skip an already-floored or empty draft (give-up / overclaim already spoke).
        if not state.responses or any(
            getattr(c, "is_floor", False) for c in state.responses
        ):
            return state
        answer = _answer_text(state)
        response_urls = _extract_urls(answer)
        if not response_urls:
            return state  # byte-identical: no URLs, nothing to ground

        # 2. DECISION — user-supplied URLs are exempt (echoing the user ≠ fabrication).
        user_urls = {_normalize_url(u) for u in _extract_urls(state.input_text)}
        user_urls.discard("")
        fetched = _fetched_source_set(state)
        fabricated = {
            norm
            for u in response_urls
            if (norm := _normalize_url(u)) and norm not in fetched and norm not in user_urls
        }
        if not fabricated:
            return state  # every URL is grounded or user-supplied — back-compat

        retrieval_ran = _retrieval_ran(state)
        log.engine.warning(
            "grounding.fabricated_citations",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "n_fabricated": len(fabricated),
                    "n_fetched": len(fetched),
                    "retrieval_ran": retrieval_ran,
                }
            },
        )

        # 3. STEP — TS5: floor when the turn RETRIEVED ZERO SOURCES. This covers BOTH
        # "no retrieval tool ran" AND the dangerous "empty scheduled cycle" — a
        # web_search that ran but came back EMPTY (ADR-T5, Mary's #1 risk: a 2-hourly
        # poke whose search found nothing must never fabricate). With no fetched
        # source, every cited URL is fabricated by definition and the external claim
        # is wholly ungrounded; stripping the URLs would leave an ungrounded HUSK of
        # invented prose (e.g. "GPT-5.6 just launched" with the link removed). Floor
        # instead. When SOME source WAS fetched, fall through and strip only the
        # fabricated URLs, keeping the grounded remainder.
        if not fetched:
            log.engine.warning(
                "grounding.floored_no_sources",
                extra={"_fields": {
                    "trace_id": state.trace_id, "retrieval_ran": retrieval_ran,
                }},
            )
            # Agentic self-correction: cited sources with nothing retrieved —
            # re-run once with the rejection fed back so the model can fetch
            # REAL sources; accepted only if the child's own gates clear it.
            # Retrieval is read-only ⇒ no double-side-effect risk.
            corrected = await _try_corrective_rerun(
                state,
                "it cited sources/URLs but NO source was actually retrieved "
                "this turn — the citations are fabricated. Retrieve real "
                "sources with web_search/web_fetch, or answer without "
                "external claims.",
            )
            if corrected is not None:
                log.engine.info(
                    "grounding.corrected",
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
                return state.evolve(responses=corrected)
            return state.evolve(
                responses=(_grounding_floor_chunk(state),), overclaim_blocked=True
            )

        stripped = tuple(
            c.model_copy(update={"content": _strip_urls(c.content, fabricated)})
            if c.kind == "answer"
            else c
            for c in state.responses
        )
        if _is_gutted(_answer_text(state.evolve(responses=stripped))):
            log.engine.warning(
                "grounding.floored_gutted",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state.evolve(
                responses=(_grounding_floor_chunk(state),), overclaim_blocked=True
            )

        # 4. EXIT — deliver the answer with fabricated citations stripped.
        log.engine.info(
            "grounding.stripped",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "n_stripped": len(fabricated),
                }
            },
        )
        return state.evolve(responses=stripped)
    except Exception as exc:
        log.engine.error(
            "[grounding_gate] internal error — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state


# =============================================================================
# overclaim_gate — surface_overclaim_gate: block a confident non-floor response
# that delivered nothing real while tools failed/bounced. STRUCTURAL (no fragile
# text analysis): reuses delivered_successes (P0) + the TPS no_progress stamp.
# Runs AFTER the give-up floor, BEFORE deliver, in both backends. Never raises.
# Emits structured overclaim.detected / overclaim.cleared so a dead gate is
# visible.
#
# PBC adds a THIRD trigger: RETRIEVAL-INTENT overclaim, the no-URL sibling of the
# grounding gate. A turn whose intent required a live lookup but that ran no
# ``web_search``/``web_fetch`` tool is answering from the model's own (possibly
# stale) knowledge with nothing to cite — floor it to the honest "I didn't
# actually look this up" instead of shipping a confident guess. The classify is
# lazy and gated (see ``_should_classify_retrieval``): it costs one fast-tier
# one-token call, ONLY on a clean, non-delivering, non-conversational turn that
# used no retrieval tool and where triggers 1/2 already cleared.
# =============================================================================

# Trigger 3 (PBC) culprit tag — the classifier-guessed "the turn's intent needed a
# live lookup but none ran" veto, distinct from a real tool-name culprit (triggers
# 1/2) so the wrapper knows which floor prose to render.
_RETRIEVAL_CULPRIT = "retrieval"

# Trigger 4 culprit tag — the classifier-guessed "the draft PROMISES future
# scheduled work but no schedules-effect tool ran" veto. The no-tool-call
# sibling of trigger 1's MEASURED unverified-effect check: trigger 1 catches an
# ATTEMPTED-but-unproven schedule (cronjob ran, verification failed); this
# catches a PURE TEXT promise where cronjob never ran at all.
_SCHEDULE_CULPRIT = "scheduling_commit"


def _is_overclaim(state: PipelineState) -> tuple[bool, str | None]:
    """Return (True, culprit) if the current draft is a structural overclaim.

    FOUR triggers (an affirmative non-floor draft fires the first that holds), in
    descending confidence order — MEASURED ledger truth beats a classifier guess:

    1. MEASURED effect veto (ADR-T2 / TS3) — the turn invoked a tool that declared a
       durable ``effect_class`` (creates_persistent_entity / sends_message / schedules)
       whose result was NOT verified==True. DEFAULT-DENY: verified∈{False, unknown} or a
       plain failure all qualify (``state.unverified_effects`` is non-empty). The burden
       is on PROOF — absence of a verified receipt vetoes a "✅ done" claim regardless of
       how richly it is phrased, so it cannot be gamed by wording. ``unknown`` is NOT
       success — it routes to the floor.
    2. STRUCTURAL give-up (the original) — nothing crossed the OUT boundary
       (``delivered_successes`` empty) AND at least one tool failed/bounced (an
       unrecovered consequential failure OR a TPS no-progress bounce).
    3. RETRIEVAL-INTENT (PBC) — classifier-stamped, lower-confidence than 1/2 above,
       so it runs LAST and only when neither MEASURED trigger fired: the turn's
       intent required a live lookup (``state.requires_retrieval``, stamped lazily by
       the async wrapper) but no retrieval tool ran this turn. The affirmative draft
       is then answering from the model's own (possibly stale) knowledge with no URL
       to inspect — the no-URL sibling of the grounding gate.
    4. SCHEDULING-COMMITMENT — classifier-stamped, evaluated alongside trigger 3 (both
       are lower-confidence guesses gated behind the MEASURED triggers): the draft's
       OWN text commits to doing something for the user LATER on a schedule
       (``state.requires_scheduling_commit``, stamped lazily by the async wrapper) but
       no ``schedules``-effect tool ran THIS TURN AT ALL
       (``"schedules" not in state.ran_effect_classes``). A real (even if unverified)
       schedule attempt is already caught by trigger 1 via ``unverified_effects``; this
       is the no-tool-call sibling — a pure text promise with zero cronjob call.

    The empty-draft and already-floor guards clear all four. A pure conversational/
    clarify turn (no effect-classed tool, no failures, no no_progress_tools, no
    retrieval-intent stamp, no scheduling-commitment stamp) is CLEARED.
    """
    if not state.responses or all(not c.content.strip() for c in state.responses):
        return (False, None)
    if any(getattr(c, "is_floor", False) for c in state.responses):
        return (False, None)
    # Trigger 1 — MEASURED: an unproven durable effect vetoes the affirmative draft
    # FIRST, before the delivery clear: a turn that delivered ONE thing but could not
    # prove it created the agent must still not claim the agent exists.
    if state.unverified_effects:
        return (True, state.unverified_effects[0])
    if state.delivered_successes:
        # Something crossed the OUT boundary — legitimate delivery.
        return (False, None)
    unrecovered = _unrecovered_consequential_failures(state)
    stuck_tools = state.no_progress_tools
    culprit = (
        next((n for n in state.consequential_failures if n in unrecovered), None)
        or (stuck_tools[0] if stuck_tools else None)
    )
    if culprit is not None:
        return (True, culprit)
    # Trigger 3 — RETRIEVAL-INTENT overclaim (classifier-stamped, lower-confidence
    # than the MEASURED triggers above, so it runs LAST and only on a clean,
    # non-delivering turn). The turn's intent required a live lookup but no
    # retrieval tool ran, so the affirmative draft is answering from the model's
    # own (stale) knowledge.
    if state.requires_retrieval and not _retrieval_ran(state):
        return (True, _RETRIEVAL_CULPRIT)
    # Trigger 4 — SCHEDULING-COMMITMENT overclaim (classifier-stamped, same
    # confidence tier as trigger 3, evaluated after it on a clean, non-delivering
    # turn). The draft's own text promises future scheduled work but no
    # schedules-effect tool ran this turn AT ALL — a pure text promise, not an
    # attempted-but-unverified schedule (trigger 1 already covers that case).
    if state.requires_scheduling_commit and "schedules" not in state.ran_effect_classes:
        return (True, _SCHEDULE_CULPRIT)
    return (False, None)


def _should_classify_retrieval(state: PipelineState) -> bool:
    """Cheap structural precondition (PBC Q3) gating the ONE classifier call.

    Confines the cost to the exact suspicious set: a non-empty, non-floored
    affirmative draft, on a non-conversational turn (the router already judged a
    ``conversational`` turn fully answerable from the model's own knowledge), that
    used no retrieval tool, and delivered nothing measurable. Any turn failing
    this precondition never pays for a classify call.
    """
    if not state.responses or all(not c.content.strip() for c in state.responses):
        return False
    if any(getattr(c, "is_floor", False) for c in state.responses):
        return False
    if state.intent_class == "conversational":
        return False
    if _retrieval_ran(state):
        return False
    return not state.delivered_successes


def _should_classify_schedule_commit(state: PipelineState) -> bool:
    """Cheap structural precondition gating the ONE trigger-4 classifier call.

    Deliberately does NOT exclude ``conversational`` turns (unlike
    ``_should_classify_retrieval``): a casual "sure, I'll ping you in five" is
    exactly the shape of the bug this trigger exists to catch — the router
    already classifies that as conversational, so excluding it would blind the
    gate to its own target case. Confines the cost to: a non-empty, non-floored
    affirmative draft that had no schedules-effect tool run this turn (a real
    schedule attempt, verified or not, is trigger 1's job, not this classifier's).
    """
    if not state.responses or all(not c.content.strip() for c in state.responses):
        return False
    if any(getattr(c, "is_floor", False) for c in state.responses):
        return False
    return "schedules" not in state.ran_effect_classes


async def _stamp_overclaim_classifiers(state: PipelineState) -> PipelineState:
    """Run trigger-3 (retrieval) + trigger-4 (schedule-commit) fast classifiers
    CONCURRENTLY — whichever pass their own precondition — and stamp both verdicts.

    The two calls are genuinely independent: trigger 3 reads the REQUEST
    (``input_text``) and stamps ``requires_retrieval``; trigger 4 reads the
    RESPONSE (``_answer_text``) and stamps ``requires_scheduling_commit`` — disjoint
    inputs, disjoint output fields, no ordering dependency. They previously ran in
    series, so a clean turn (the common case: neither triggers) paid BOTH latencies
    back-to-back on the pre-deliver hot path. Under one ``asyncio.gather`` that clean
    case now costs ``max`` of the two calls, not their sum. Each classifier is read
    off ``get_services()`` (``None`` unwired ⇒ that field keeps its ``False``
    default) and is itself fail-safe (-> False on every degraded path), so a gathered
    branch never raises. A branch whose precondition is unmet is not launched at all,
    preserving the "pay only for the checks a turn actually needs" contract.
    """
    services = get_services()
    ric = services.retrieval_intent_classifier
    scc = services.schedule_commit_classifier
    run_retrieval = ric is not None and _should_classify_retrieval(state)
    run_schedule = scc is not None and _should_classify_schedule_commit(state)

    async def _retrieval() -> bool:
        if not run_retrieval:
            return state.requires_retrieval
        assert ric is not None  # narrowed by run_retrieval
        return await ric.requires_lookup(request=state.input_text)

    async def _schedule() -> bool:
        if not run_schedule:
            return state.requires_scheduling_commit
        assert scc is not None  # narrowed by run_schedule
        return await scc.commits_to_future_schedule(response=_answer_text(state))

    requires_retrieval, requires_scheduling_commit = await asyncio.gather(
        _retrieval(), _schedule(),
    )
    return state.evolve(
        requires_retrieval=requires_retrieval,
        requires_scheduling_commit=requires_scheduling_commit,
    )


async def surface_overclaim_gate(state: PipelineState) -> PipelineState:
    """Replace a confident overclaim draft with an honest floor.

    Called AFTER surface_consequential_giveup_floor and BEFORE persist_turn /
    deliver in both backends. Never raises — any internal error is logged and the
    original state is returned unchanged (fail-open: no silent suppression of a
    valid response).

    Trigger 3 (PBC) adds ONE lazy classifier call: ``_is_overclaim`` is evaluated
    first with the default ``requires_retrieval=False`` (triggers 1/2 are free,
    MEASURED checks); only when it clears AND the Q3 precondition holds does the
    wrapper spend a single fast one-token call to stamp ``requires_retrieval``
    before re-evaluating. A turn that already overclaimed via trigger 1/2, or that
    fails the precondition (conversational, retrieved, delivered, empty/floored),
    never reaches the classifier.

    Trigger 4 adds a SECOND lazy classifier call, same shape and its own
    precondition (no schedules-effect tool ran this turn). Triggers 3 + 4 are
    independent (request-side vs response-side) so when both preconditions hold
    they run CONCURRENTLY under one ``asyncio.gather`` (see
    ``_stamp_overclaim_classifiers``) rather than in series — a turn still pays for
    zero, one, or both, but never their latencies back-to-back.
    """
    try:
        is_oc, culprit = _is_overclaim(state)
        if not is_oc and (
            _should_classify_retrieval(state) or _should_classify_schedule_commit(state)
        ):
            # Triggers 3 + 4 are independent fast-classifier checks — run whichever
            # are needed CONCURRENTLY (see _stamp_overclaim_classifiers) rather than
            # paying their latency in series, then re-evaluate the overclaim verdict.
            state = await _stamp_overclaim_classifiers(state)
            is_oc, culprit = _is_overclaim(state)
        if not is_oc:
            log.engine.debug(
                "overclaim.cleared",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
        log.engine.warning(
            "overclaim.detected",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "failed_capability": culprit,
                }
            },
        )
        if culprit == _RETRIEVAL_CULPRIT:
            # Agentic self-correction: the draft answered from stale internal
            # knowledge when the request needed a live lookup. Instead of only
            # confessing "I didn't actually look this up", re-run the turn ONCE
            # with the rejection fed back — the corrective child can call the
            # retrieval tools for real, and is accepted only if its own gates
            # clear it. Read-only tools ⇒ no double-side-effect risk. Failure ⇒
            # the existing honest floor, unchanged.
            corrected = await _try_corrective_rerun(
                state,
                "it answered from memory when the request required a LIVE web "
                "lookup, and no retrieval tool was called.",
            )
            if corrected is not None:
                log.engine.info(
                    "overclaim.corrected",
                    extra={"_fields": {"trace_id": state.trace_id, "culprit": culprit}},
                )
                return state.evolve(responses=corrected)
        if culprit == _SCHEDULE_CULPRIT:
            # Do-the-action upgrade: before confessing "I didn't actually
            # schedule anything", TRY to actually schedule it — extract the
            # promised (goal, schedule) and mint the job through the real
            # CronjobTool (same guarded path the model should have called).
            # On success the promise is backed by a verified job row: keep the
            # original answer, append a truthful receipt, and the overclaim is
            # RESOLVED (not blocked). Only when fulfillment fails does the
            # honest ask-floor fire, exactly as before.
            receipt = await _try_fulfill_schedule_commit(state)
            if receipt is not None:
                log.engine.info(
                    "overclaim.fulfilled",
                    extra={"_fields": {"trace_id": state.trace_id, "culprit": culprit}},
                )
                receipt_chunk = ResponseChunk(
                    content=receipt,
                    is_final=False,
                    chunk_index=len(state.responses),
                    trace_id=state.trace_id,
                    owl_name=state.owl_name,
                )
                return state.evolve(responses=(*state.responses, receipt_chunk))
        floor = (
            _grounding_floor_chunk(state)
            if culprit == _RETRIEVAL_CULPRIT
            else _schedule_commit_floor_chunk(state)
            if culprit == _SCHEDULE_CULPRIT
            else _floor_chunk(state, culprit)
        )
        return state.evolve(responses=(floor,), overclaim_blocked=True)
    except Exception as exc:
        log.engine.error(
            "[overclaim_gate] internal error — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state


# =============================================================================
# critical_failure — surface_critical_failure: surface CRITICAL pipeline-step
# failures to the user (Phase 2 #2).
#
# The pipeline backends self-heal: a step exception is logged at ERROR, appended
# to ``state.errors``, and the loop CONTINUES. That is correct for NON-critical
# steps (assemble/classify degrade gracefully). But when the CRITICAL,
# answer-producing step (``execute``) fails AND no usable response was produced,
# the user is otherwise left with silence — no indication anything broke.
#
# This module provides a SHARED helper both the asyncio and langgraph backends
# call just before ``deliver``. It detects a critical failure and, if found,
# injects a single user-facing apology ResponseChunk so ``deliver`` sends it.
#
# Multilingual constraint (project rule — there is NO i18n system): the apology
# is generated in the USER'S language via the provider cascade (a healthy fallback
# provider may answer even though the one that failed is OPEN). If that ALSO fails
# (total outage), a neutral, language-agnostic last-resort marker is used.
# Known limitation: the last-resort line is not localized (no i18n infrastructure).
#
# No-hidden-errors: the failure is now VISIBLE to the user, not just in logs. The
# helper itself is self-healing — its own failures fall back to the neutral
# message and it NEVER raises into the backend.
# =============================================================================

# The answer-producing step(s). A failure here with no usable response is what
# leaves the user in silence; non-critical steps self-heal and stay silent.
_CRITICAL_STEPS: frozenset[str] = frozenset({"execute"})

# Delegation statuses that mean the parent received NO usable sub-task answer.
# ``ok`` and ``recovered_via_secretary`` indicate the model DID get content.
# ``truncated`` has partial content — treat as an answer; do not surface.
# ``refused`` is a safety rail the model recovers from inline; do not surface.
_DELEGATION_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"timeout", "child_error", "empty", "cycle", "target_not_found"}
)

# Tier to start the apology cascade from. A different/healthy provider in the
# cascade may answer even when the owl's own provider tripped its breaker.
_APOLOGY_TIER = "fast"

# Language-agnostic last-resort. Used ONLY when the cascade apology itself fails
# (total provider outage). A warning sign + a short stable marker + the failure
# class for debuggability. Not localized — documented known limitation (no i18n).
_NEUTRAL_PREFIX = "⚠ "  # warning sign


def _has_usable_response(state: PipelineState) -> bool:
    """True when at least one accumulated chunk carries a GENUINE (non-floor) answer.

    The execute site writes a deterministic never-empty FLOOR chunk (``is_floor``)
    as the zero-provider backstop. A response made up ONLY of floor chunks is the
    honest last resort — NOT a real answer — so it must NOT short-circuit the
    critical-failure cascade: a localized LLM apology (better UX) should still get
    the chance to REPLACE it while any provider is alive. A genuine chunk (non-empty
    content, ``is_floor`` False) is a real answer and DOES short-circuit the cascade.
    """
    return any(c.content and not c.is_floor for c in state.responses)


def _has_floor_only(state: PipelineState) -> bool:
    """True when the response is non-empty but consists SOLELY of floor chunks.

    This is the replaceable backstop: the cascade ran because there is no genuine
    answer, yet a deterministic floor is already present. When the cascade produces
    a localized apology we DROP these floor chunks and substitute the apology; when
    the cascade ALSO fails we KEEP them (the floor already supersedes the neutral
    ``⚠ [marker]`` as the better honest fallback).
    """
    floors = [c for c in state.responses if c.content and c.is_floor]
    return bool(floors) and not _has_usable_response(state)


def _critical_failure_classes(state: PipelineState) -> list[str]:
    """Return the failure class for each critical step that recorded an error.

    REACT-7/F092 — PRIMARY source is the STRUCTURED ``state.step_errors`` records
    (typed step + exc_type), so a drift in the human error STRING never breaks
    critical-failure detection. The string parser (via the SHARED step_error helper,
    not an inline literal) is the back-compat fallback for any legacy error string
    written outside the structured seam.
    """
    classes: list[str] = []
    for rec in state.step_errors:
        if rec.step in _CRITICAL_STEPS:
            classes.append(rec.exc_type or "error")
    # Fallback: parse any legacy/free string for a critical step (e.g. errors
    # appended without a structured record). De-dup against structured records by
    # only parsing strings when they name a critical step the structured set missed.
    structured_msgs = {(r.step, r.exc_type) for r in state.step_errors}
    for err in state.errors:
        parsed = parse_step_error(err)
        if parsed is None:
            continue
        step, exc_type, _msg = parsed
        if step in _CRITICAL_STEPS and (step, exc_type) not in structured_msgs:
            classes.append(exc_type or "error")
    return classes


def _delegation_failed_with_no_answer(state: PipelineState) -> bool:
    """True when a delegate_task call recorded a terminal status AND the parent
    produced no usable answer — the swallowed-delegation failure case.

    Guards:
    * Returns False immediately if the parent has any usable response (the model
      recovered on its own — do NOT inject an apology over a real answer).
    * Scans ``state.tool_calls`` for records whose parsed JSON carries
      ``{"record": {"status": <terminal>}}``; returns True on the first match.
    * JSON parsing is DEFENSIVE — any parse error or unexpected shape is skipped;
      the helper never raises (B5: the safety net must not crash the pipeline).
    """
    if _has_usable_response(state):
        return False
    for tc in state.tool_calls:
        if tc.result is None:
            continue
        try:
            parsed = json.loads(tc.result)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue  # unexpected shape (e.g. a bare JSON array) — skip, never raise
        record = parsed.get("record")
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if status in _DELEGATION_TERMINAL_STATUSES:
            return True
    return False


def detect_critical_failure(state: PipelineState) -> bool:
    """True when a CRITICAL step recorded an error AND there is no usable response,
    OR when a delegate_task call swallowed a terminal failure with no parent answer.

    Both conditions for the execute-error path are required: a critical step that
    errored but still produced a partial answer (e.g. token-limit truncation) is
    NOT silence, so we don't inject an apology over a real (if partial) response.
    The delegation-failure predicate applies the same guard (``_has_usable_response``
    is the first check in both helpers).
    """
    if _has_usable_response(state):
        return False
    return bool(_critical_failure_classes(state)) or _delegation_failed_with_no_answer(state)


async def _generate_localized_apology(
    state: PipelineState, services: StepServices,
) -> str | None:
    """Best-effort: a ONE-sentence apology in the user's language via the cascade.

    Returns the apology text, or None if no provider could be reached / it failed.
    Never raises — the caller falls back to the neutral marker on None.
    """
    registry = services.provider_registry
    if registry is None:
        log.engine.debug(
            "[critical_failure] apology: no provider_registry — neutral fallback",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    # Minimal prompt; the model must answer in the SAME language as the user.
    system_text = (
        "You write a single short apology sentence in the SAME language as the "
        "user's message. No preamble, no explanation, no quotes — one sentence only."
    )
    user_text = (
        f"The user said: {state.input_text}\n"
        "Reply with ONE short sentence, in the SAME language as the user, "
        "apologizing that their request could not be completed right now."
    )
    messages = [
        Message(role="system", content=system_text),
        Message(role="user", content=user_text),
    ]

    # F-8: walk tiers from the apology tier; if a provider's ``complete`` raises or
    # returns empty mid-outage, ADVANCE to the next tier's provider before falling
    # to the non-localized neutral marker. Reuses the registry's circuit-aware
    # cascade per tier; providers are de-duped by identity so one shared across
    # tiers isn't retried, and only ONE tried provider is attempted per tier.
    if _APOLOGY_TIER in _TIER_ORDER:
        start = _TIER_ORDER.index(_APOLOGY_TIER)
        tier_walk = _TIER_ORDER[start:] + _TIER_ORDER[:start]
    else:
        tier_walk = _TIER_ORDER
    tried: set[int] = set()
    for tier in tier_walk:
        try:
            provider, model = registry.get_with_cascade(tier)
        except Exception as exc:  # AllProvidersUnavailableError or any lookup failure
            log.engine.warning(
                "[critical_failure] apology: cascade found no provider for tier — advancing",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id, "tier": tier}},
            )
            continue
        if id(provider) in tried:
            continue  # same provider serves multiple tiers — don't re-attempt
        tried.add(id(provider))
        try:
            # disable_thinking: a one-sentence apology needs no chain-of-thought;
            # a reasoning-capable provider otherwise burns its budget on <think>
            # and never emits the sentence (same empty-reply failure mode fixed
            # in owls/router.py). No max_tokens cap (owner decision 2026-07-22).
            result = await provider.complete(
                messages, model=model, disable_thinking=True,
            )
        except Exception as exc:  # provider call itself failed (outage mid-cascade)
            log.engine.warning(
                "[critical_failure] apology: provider.complete failed — advancing tier",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id, "tier": tier}},
            )
            continue
        text = (result.content or "").strip()
        if not text:
            log.engine.warning(
                "[critical_failure] apology: provider returned empty — advancing tier",
                extra={"_fields": {"trace_id": state.trace_id, "tier": tier}},
            )
            continue
        log.engine.info(
            "[critical_failure] apology: localized message generated",
            extra={"_fields": {"trace_id": state.trace_id, "tier": tier, "model": model, "len": len(text)}},
        )
        return text

    log.engine.warning(
        "[critical_failure] apology: all tiers exhausted — neutral fallback",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return None


def _neutral_fallback(state: PipelineState) -> str:
    """Localized last-resort message (F089/F098).

    The leading prose is now the localized ``self_heal_floor_minimal`` for the
    turn's language (``localize`` en-fallbacks safely for any uncatalogued lang).
    A compact ``[<ExcType>]`` marker is still appended for debuggability — that
    bracket is a technical innard inside a localized frame, not translated."""
    classes = _critical_failure_classes(state)
    marker = classes[0] if classes else "error"
    prose = localize("self_heal_floor_minimal", state.language)
    return f"{_NEUTRAL_PREFIX}{prose} [{marker}]"


def _incident_note(state: PipelineState, services: StepServices) -> str | None:
    """ADR-6 Task 7 — one-line note when this turn's critical failure matches
    an OPEN, VERIFIED background-incident RCA verdict.

    Reuses ``services`` — the SAME metadata parameter ``surface_critical_failure``
    already reads (``services.provider_registry`` etc.) — as the channel for the
    incident summary, per the task brief: no new gate, no new cascade member,
    just enriching the text this EXISTING surfacer already produces. Keyed on
    the same ``failure_class`` string both ``_critical_failure_classes`` (this
    module) and ``RcaVerdict.failure_class`` (an exception class name derived
    the same way, via ``classify_failure``) use. Never raises; ``None`` when
    unwired or nothing matches — byte-identical to today.
    """
    lookup = services.incident_verdict_lookup
    if lookup is None:
        return None
    try:
        for fc in _critical_failure_classes(state):
            verdict = lookup(fc)
            if verdict is not None and verdict.verified:
                return (
                    "(This looks like a known, already-investigated issue — "
                    f"{verdict.root_cause.strip()[:200]})"
                )
    except Exception as exc:  # B5 — an enrichment failure must never break surfacing
        log.engine.warning(
            "[critical_failure] incident_note: lookup failed — omitting",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
    return None


async def surface_critical_failure(
    state: PipelineState, services: StepServices,
) -> PipelineState:
    """If a critical step failed with no response, inject a user-facing apology.

    Returns the (possibly evolved) state to deliver. Self-healing: NEVER raises —
    on any internal failure it still returns a state carrying the neutral marker
    so the user is never left in silence. Must run BEFORE ``deliver.run(...)``.
    """
    try:
        if not detect_critical_failure(state):
            return state
        log.engine.warning(
            "[critical_failure] surfacing: critical step failed with no response",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "failure_classes": _critical_failure_classes(state),
                "error_count": len(state.errors),
            }},
        )
        floor_only = _has_floor_only(state)
        text = await _generate_localized_apology(state, services)
        if not text:
            # Cascade failed (no healthy provider). If a deterministic floor is
            # already present, KEEP it — it is the honest zero-provider backstop and
            # already supersedes the neutral ``⚠ [marker]``. Only when there is NO
            # floor (e.g. the swallowed-delegation path) do we emit the neutral
            # last-resort so the user is never left in silence.
            if floor_only:
                log.engine.warning(
                    "[critical_failure] surfacing: cascade down — keeping deterministic floor",
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
                return state
            text = _neutral_fallback(state)
            log.engine.warning(
                "[critical_failure] surfacing: using neutral last-resort (no i18n)",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
        note = _incident_note(state, services)
        if note:
            text = f"{text} {note}"
        chunk = ResponseChunk(
            content=text,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        # When the cascade produced a localized apology AND a floor backstop is
        # present, the apology is the preferred layer: DROP the floor chunk(s) and
        # substitute the apology. ``errors`` is never touched here — the responses-only
        # invariant holds, so durable status / A2A / parliament still see a FAILURE.
        if floor_only:
            kept = tuple(c for c in state.responses if not c.is_floor)
            return state.evolve(responses=(*kept, chunk))
        return state.evolve(responses=(*state.responses, chunk))
    except Exception as exc:  # B5 — the surfacing helper must never break the run
        log.engine.error(
            "[critical_failure] surfacing: helper failed — emitting neutral marker",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        try:
            chunk = ResponseChunk(
                content=_neutral_fallback(state),
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            return state.evolve(responses=(*state.responses, chunk))
        except Exception:  # truly last resort — return state untouched, log only
            log.engine.error(
                "[critical_failure] surfacing: neutral injection also failed",
                exc_info=True,
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state


# =============================================================================
# persistence_handoff — surface_persistence_handoff: the "hand-to-better-owl"
# rung of the never-give-up ladder.
#
# When a turn would otherwise give up (consequential failure unachieved, or a
# no-progress spiral), FIRST try to hand the whole request to a better-fit owl —
# resolved by capability (semantic skill recall + PA4b skill ownership) — and
# deliver ITS answer. If no better owl exists, or the hand-off does not produce a
# real answer, leave the responses untouched so the honest floor fires next
# ("honest if no better owl").
#
# Bounded: ONE hand-off per turn (runs once in the pre-delivery band), only at
# delegation depth 0 (a delegated child never re-hands-off — recursion guard), and
# never when the budget is exhausted. Runs IMMEDIATELY BEFORE
# ``surface_consequential_giveup_floor`` in both delivery backends: a failed
# hand-off still floors honestly; a successful hand-off replaced the responses so
# the floor's ``decide_delivery`` no longer sees a give-up and no-ops.
#
# B5: never raises — on ANY problem it logs and returns ``state`` unchanged, so the
# hand-off can never break delivery.
# =============================================================================


async def _resolve_better_owl(
    state: PipelineState, services: StepServices
) -> str | None:
    """The first capability-matching owl (!= the current owl) that can take over.

    Ranks skills by cosine over the turn's query embedding, maps each skill to its
    owning owl (PA4b skill_ownership rows + built-in ``manifest.skills``), and
    returns the highest-ranked owner that is registered and not the current owl.
    None ⇒ no better-fit owl (caller falls through to the honest floor)."""
    store = services.skill_store
    db_pool = services.db_pool
    registry = services.owl_registry
    # Gate 2 (continued): these are required to capability-match. Bound check is in
    # the caller; here they cannot be None on the path that reaches us, but guard
    # anyway (B5 — never assume wiring).
    if store is None or db_pool is None or registry is None or state.query_embedding is None:
        log.engine.debug(
            "[persistence_handoff] resolve: missing skill-store/db/registry/embedding — no target",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    recalled = await store.semantic_recall(list(state.query_embedding), limit=5)
    if not recalled:
        log.engine.debug(
            "[persistence_handoff] resolve: no semantic skill matches — no target",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    # skill_name -> owl_name, from BOTH the durable ownership rows (PA4b) AND the
    # built-in ownership already on manifests (an owl may own a skill via
    # manifest.skills with no skill_ownership row). First owner wins.
    skill_to_owl: dict[str, str] = {}
    owned = await read_all_skill_ownership(db_pool)
    for owl_name, skill_names in owned.items():
        for skill_name in skill_names:
            skill_to_owl.setdefault(skill_name, owl_name)
    registered = {m.name for m in registry.list()}
    for manifest in registry.list():
        for skill_name in manifest.skills:
            skill_to_owl.setdefault(skill_name, manifest.name)

    for skill, _sim in recalled:
        owner = skill_to_owl.get(skill.name)
        if owner is None or owner == state.owl_name or owner not in registered:
            continue
        log.engine.debug(
            "[persistence_handoff] resolve: target found",
            extra={"_fields": {"trace_id": state.trace_id, "skill": skill.name, "target": owner}},
        )
        return owner

    log.engine.info(
        "[persistence_handoff] no better-fit owl — falling through to floor",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return None


async def surface_persistence_handoff(
    state: PipelineState, services: StepServices
) -> PipelineState:
    """Hand a would-give-up turn to a better-fit owl and deliver its answer.

    1. ENTRY — log; gate on the give-up verdict (healthy turns are byte-identical no-ops).
    2. DECISION — bound gates (depth 0, budget remaining, delegation + embedding wired)
       then resolve a capability-matched target owl.
    3. STEP — one bounded delegation round-trip to that owl.
    4. EXIT — replace responses with the child's answer on success; else return
       state unchanged so the honest floor fires next.
    B5 catch: never raises; logs and returns state untouched.
    """
    try:
        # 1. ENTRY + give-up gate. CRITICAL: a non-give-up turn returns immediately,
        # so a healthy turn is byte-identical (one extra decide_delivery call, which
        # the floor makes anyway one step later).
        log.engine.debug(
            "[persistence_handoff] surface_persistence_handoff: entry",
            extra={"_fields": {"trace_id": state.trace_id, "owl": state.owl_name}},
        )
        is_giveup = decide_delivery(state).consequential_giveup or is_no_progress_giveup(state)
        if not is_giveup:
            return state

        # 2. DECISION — bound gates. Any failure → fall through to the honest floor.
        if state.delegation_depth != 0:
            log.engine.debug(
                "[persistence_handoff] depth>0 — no hand-off (recursion guard)",
                extra={"_fields": {"trace_id": state.trace_id, "depth": state.delegation_depth}},
            )
            return state
        if state.budget_capped:
            log.engine.debug(
                "[persistence_handoff] budget exhausted — straight to floor",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
        if services.a2a_delegator is None:
            log.engine.debug(
                "[persistence_handoff] no a2a_delegator wired — no hand-off",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
        if state.query_embedding is None:
            log.engine.debug(
                "[persistence_handoff] no query embedding — cannot capability-match",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state

        target = await _resolve_better_owl(state, services)
        if target is None:
            return state

        # 3. STEP — one bounded hand-off. parent_state built the SAME way
        # delegate_task._run_delegation builds it: depth 0 (gated above), the
        # creation_ceiling clamped to the parent's effective bounds, responses/tool
        # state cleared so the child starts fresh. The delegator increments depth →
        # the child runs at depth 1 and cannot re-hand-off.
        #
        # CRITICAL: clear the parent's give-up SNAPSHOT (we only got here BECAUSE the
        # parent gave up). _run_specialist evolves from this state and does NOT reset
        # these, so without clearing them the child would inherit the PARENT's failed
        # consequential tally + no-progress flags and its own floor would fire on the
        # parent's failure — defeating the hand-off. The proven delegate_task path
        # avoids this by building a fresh PipelineState; we reset to the same effect.
        parent_state = state.evolve(
            responses=(),
            tool_calls=(),
            errors=(),
            consequential_failures=(),
            consequential_successes=(),
            recovered_consequential=(),
            delivered_successes=(),
            turn_made_progress=True,
            no_progress_tools=(),
            pipeline_step="dispatch",
            creation_ceiling=child_floor(
                state.owl_name, state.creation_ceiling, services.owl_registry
            ),
        )
        log.engine.info(
            "[persistence_handoff] handing off to better-fit owl",
            extra={"_fields": {"trace_id": state.trace_id, "from": state.owl_name, "to": target}},
        )
        res = await services.a2a_delegator.delegate(
            from_owl=state.owl_name,
            to_owl=target,
            sub_task=state.input_text,
            parent_state=parent_state,
        )

        # 4. EXIT — deliver the child's real answer, else fall through to the floor.
        if res.status == "ok" and res.content.strip():
            chunk = ResponseChunk(
                content=res.content + provenance_footer(target),
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
                is_floor=False,  # a REAL answer from the better owl, not a floor
            )
            log.engine.info(
                "[persistence_handoff] hand-off delivered — replacing draft with target's answer",
                extra={"_fields": {"trace_id": state.trace_id, "to": target}},
            )
            return state.evolve(responses=(chunk,))
        log.engine.info(
            "[persistence_handoff] hand-off did not produce an answer — honest floor next",
            extra={"_fields": {"trace_id": state.trace_id, "to": target, "status": res.status}},
        )
        return state
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[persistence_handoff] failed — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
