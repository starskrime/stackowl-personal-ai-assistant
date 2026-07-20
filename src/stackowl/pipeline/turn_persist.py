"""persist_turn — store the user+assistant turn AFTER the honest floor band (F088).

F088 (P0): consolidate (step 6) used to persist the assistant draft BEFORE the
honest floor band replaced it, so the memory bridge stored the dressed-up "I did
it" draft — which the dream worker later promoted to a durable fact (a lie
laundered into committed knowledge). The fix relocates persistence to run AFTER
``surface_consequential_giveup_floor`` + ``surface_critical_failure`` so it reads
the POST-floor responses.

When the turn FLOORED (a consequential give-up, a critical-failure apology, or a
provider/pipeline floor chunk), we do NOT persist the assistant prose at all — we
record ONLY the user utterance. There is then no dressed-up draft in the durable
record for the dream worker to promote (LM-3). A clean success persists the real
delivered text trust="self"; a tool-merge success persists trust="untrusted" via
the SP-2 ``state.merged_external`` stamp (read here, NEVER recomputed from
post-floor responses — the trust-laundering guard, LM-2/LM-9).

B5: best-effort — never raises into the backend (memory persistence MUST NOT
block delivery). Must run SYNCHRONOUSLY inside the turn ledger ContextVar binding
(``is_consequential_giveup_now`` reads it) — never detached as a create_task (LM-4).
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.memory.trust import Trust
from stackowl.pipeline.delivery_gate import (
    _attempts_for_state,
    _critical_failure_classes,
    is_consequential_giveup_now,
)
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState


def _turn_floored(state: PipelineState) -> bool:
    """True when this turn was floored — so the assistant prose must NOT persist.

    Floor-origin signals (any one):
      * any response chunk carries ``is_floor`` (SP-1 giveup floor OR the execute
        deterministic backstop OR a kept provider/critical floor);
      * a consequential give-up was detected this turn (reads the turn ledger);
      * a CRITICAL answer-step (``execute``) recorded an error — the
        critical-failure path. We scan ONLY critical-step error classes (the SAME
        helper the surfacing module uses) and NOT raw ``state.errors``, so a benign
        non-critical degradation (assemble/classify) on an otherwise delivered turn
        is NOT treated as floored and its real answer is still persisted. This is
        robust to the apology having already been substituted (which clears the
        is_floor chunk but leaves the execute error in ``state.errors``).
    Pure read; never raises (the caller's B5 wrapper is the backstop).
    """
    if any(c.is_floor for c in state.responses):
        return True
    if is_consequential_giveup_now():
        return True
    return bool(_critical_failure_classes(state))


async def persist_turn(state: PipelineState) -> None:
    """Persist the POST-floor turn as a staged conversation fact (best-effort).

    1. ENTRY — resolve services; a floored turn also enqueues a retry_queue row
       (independent of memory_bridge availability — losing the memory bridge
       must not also lose the retry-queue signal).
    2. DECISION — floored vs clean; trust from SP-2 merged_external.
    3. STEP — store user-only (floored) or user+assistant (clean) content.
    4. EXIT — log; never raise (B5).
    """
    # 1. ENTRY
    services = get_services()

    # Floor-origin signal computed once, up front: gates BOTH the retry_queue
    # bookkeeping below and the memory-bridge content/trust decision further
    # down. A floored turn must never persist the (possibly dressed-up)
    # assistant prose as a promotable fact (LM-3) — see _turn_floored.
    floored = _turn_floored(state)
    if floored:
        retry_store = getattr(services, "retry_queue_store", None)
        # retry_replay=True means THIS turn is already RetryActuator's own replay
        # of an existing retry_queue row — its floor is tracked by that row's own
        # attempt_count/_MAX_ATTEMPTS (retry_actuator.py's mark_attempt_failed),
        # not a fresh row. Without this guard, insert_pending() (no dedup, always
        # a brand-new attempt_count=0 row due immediately) fires on every replay's
        # floor too, defeating the cap and compounding into an unbounded loop.
        if retry_store is not None and not state.retry_replay:
            try:
                # Dedup against an already-pending row for this session (live
                # incident 2026-07-16): insert_pending() has no dedup, so every
                # floored turn tonight minted its OWN independent row — each one
                # later fires on its own via the 1-minute sweep, unprompted and
                # disconnected from whatever the user is discussing by then,
                # reading as the agent contradicting/forgetting itself. One
                # in-flight retry per session is enough; a second floor while one
                # is already pending piles onto the confusion instead of helping.
                existing = await retry_store.get_latest_pending_for_session(state.session_id)
                if existing is not None:
                    log.scheduler.info(
                        "[pipeline] persist_turn: retry already pending for session — "
                        "skipping duplicate row",
                        extra={"_fields": {
                            "trace_id": state.trace_id, "existing_retry_id": existing.id,
                        }},
                    )
                else:
                    banned = _attempts_for_state(state)
                    await retry_store.insert_pending(
                        trace_id=state.trace_id,
                        session_id=state.session_id,
                        goal=state.input_text,
                        banned_capabilities=list(banned) if banned else [],
                    )
            except Exception as exc:  # B5 — retry-queue bookkeeping must never block delivery
                log.scheduler.error(
                    "[pipeline] persist_turn: retry_queue insert failed",
                    exc_info=exc,
                    extra={"_fields": {"trace_id": state.trace_id}},
                )

    bridge = services.memory_bridge
    if bridge is None:
        log.memory.debug(
            "[pipeline] persist_turn: no memory bridge — skipping",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return

    # "".join, not "\n".join (same rule as retry_actuator.py): a STREAMED turn
    # has one ResponseChunk per token, so a newline join persists
    # "word\nword\nword" into memory/history — recalled into future context,
    # the model then MIMICS the one-word-per-line format in live replies
    # (confirmed production incident: telegram turns degenerated to
    # newline-after-every-word until fresh context outweighed the poison).
    assistant_text = "".join(c.content for c in state.responses if c.content).strip()
    if not state.input_text and not assistant_text:
        log.memory.debug(
            "[pipeline] persist_turn: empty turn — skipping",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return

    # 2. DECISION — a floored turn must NOT persist the (possibly dressed-up)
    # assistant prose as a promotable fact. Record ONLY the user utterance so the
    # dream worker has no "I couldn't / I did it" draft to promote (LM-3).
    if floored:
        if not state.input_text:
            # Nothing safe to persist (no user utterance, floored assistant text suppressed).
            log.memory.info(
                "[pipeline] persist_turn: floored turn with no user utterance — persisting nothing",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return
        content = f"User: {state.input_text}"
        # Untrusted: a floored turn's record is never a confident self-authored fact.
        trust_override: Trust | None = "untrusted"
        log.memory.info(
            "[pipeline] persist_turn: floored turn — persisting user utterance only (no draft)",
            extra={"_fields": {"trace_id": state.trace_id, "session_id": state.session_id}},
        )
    else:
        content = f"User: {state.input_text}\n\nAssistant: {assistant_text}"
        # SP-2 — trust from the carried merge decision (NEVER recomputed from
        # post-floor responses): tool-merged external content → untrusted, else self.
        trust_override = "untrusted" if state.merged_external else None
        log.memory.debug(
            "[pipeline] persist_turn: clean turn — persisting user+assistant",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "session_id": state.session_id,
                "merged_external": state.merged_external,
            }},
        )

    # 3. STEP — best-effort store (B5: never raise; never block delivery).
    try:
        await bridge.store(content, state.session_id, trust=trust_override)
    except Exception as exc:  # B5 — memory persistence must not break the turn
        log.memory.warning(
            "[pipeline] persist_turn: store failed — skipping",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "session_id": state.session_id}},
        )
        return
    # 4. EXIT
    log.memory.debug(
        "[pipeline] persist_turn: exit",
        extra={"_fields": {"trace_id": state.trace_id, "floored": floored}},
    )
