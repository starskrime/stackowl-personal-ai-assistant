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
    _critical_failure_classes,
    is_consequential_giveup_now,
)
from stackowl.pipeline.services import get_services, owner_scope_key
from stackowl.pipeline.state import PipelineState
from stackowl.sessions.models import is_machine_lane


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


def _floor_reason(state: PipelineState) -> str:
    """Best-effort human-readable reason a floored turn's message_ledger row failed.

    Prefers the structured critical-step failure classes (same source
    ``_turn_floored`` itself reads); falls back to the other two floor
    signals so a row is never left with an empty reason.
    """
    classes = _critical_failure_classes(state)
    if classes:
        return ",".join(classes)
    if is_consequential_giveup_now():
        return "consequential_giveup"
    return "floor_response"


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
        # A floored turn just proved the session's sticky-routed "conversational"
        # classification (triage.py FR-9) was wrong for this thread — the turn had
        # no tools wired and nothing to back up what it said. Evict so the NEXT
        # short message gets a real SecretaryRouter call instead of inheriting the
        # same tool-free routing and floor-again cycle (live incident 2026-07-21:
        # a "check on Brain" thread stayed sticky-conversational across a floor AND
        # its own retry replay, which then had no tools either).
        sticky_cache = getattr(services, "sticky_route_cache", None)
        if sticky_cache is not None:
            sticky_cache.evict(state.session_key)

        # A FLOORED TURN RETRIES ON ITS OWN ROW — this function mints NOTHING.
        #
        # It used to enqueue a SECOND durable task here (`retry-{trace_id}`, born
        # `pending`, no lease, no next_attempt_at), which bypassed the invariant
        # `enqueue_turn_task` states in its own docstring: "Exactly one producer
        # must exist: a turn both run by the fast path and claimed by the loop is
        # answered TWICE, and on Telegram the user sees two replies to one
        # question." Measured on trace e6c1d3e1, 2026-08-29 — enqueued 17:25:37,
        # claimed by the loop 17:25:38 while the fast path was still inside
        # deliver, which sent at 17:25:40; the loop's answer arrived at 17:26:01 as
        # a second message. Bakir reported it as "it always sends failed request
        # first then after some time I am getting final answer".
        #
        # The floor is now expressed where every other unachieved outcome already
        # is: `turn_task.floored_turn_of` -> `complete_turn_task` ->
        # `fail_and_requeue` on THE ROW THAT ALREADY EXISTS, with the ceiling and
        # dead-letter escalation it already has. One row per turn, one status, one
        # producer.
        #
        # THE TWO INCIDENTS THE OLD BLOCK PRESERVED ARE STILL PAID FOR, by the
        # single row rather than by a dedup rule over many:
        #   2026-07-16 — every floored turn minting its own row, each firing
        #     independently, read as the agent contradicting itself. There is now
        #     one row per turn by construction, so there is nothing to deduplicate.
        #   2026-07-21 — a second floor while one was pending was silently DROPPED.
        #     A repeat floor on the same turn is now another failed attempt against
        #     the same row's attempt_count, which is what bounds it.
        #
        # AND WHY THE FLOOR IS STILL DELIVERED IMMEDIATELY: 75% of floored turns are
        # provider-cascade outages, where a retry runs on the same dead backend and
        # floors too, and historically only ~27% of floored turns had any retry
        # behind them at all. Suppressing the floor would turn "I can't reach my
        # provider" into silence. The user keeps the honest apology; what changed is
        # that only ONE producer owns the follow-up.

    # Message-ledger terminal flip — reuses the SAME `floored` signal computed
    # above, no new failure-detection logic. Independent of the memory-bridge
    # availability check below (losing the memory bridge must not also lose
    # the message-ledger signal).
    ledger = getattr(services, "message_ledger_store", None)
    if ledger is not None:
        try:
            if floored:
                await ledger.mark_failed(state.trace_id, reason=_floor_reason(state))
            else:
                await ledger.mark_completed(state.trace_id)
        except Exception as exc:  # B5 — ledger bookkeeping must never block delivery
            log.scheduler.error(
                "[pipeline] persist_turn: message_ledger flip failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id, "floored": floored}},
            )

    bridge = services.conversation_store
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
            extra={"_fields": {"trace_id": state.trace_id, "session_key": state.session_key}},
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
                "session_key": state.session_key,
                "merged_external": state.merged_external,
            }},
        )

    # 3. STEP — the transcript first, because the staged-fact store below returns
    # early on failure and the two are independent records of the same turn.
    await _record_transcript(state, floored, assistant_text)

    # 3b. DECISION — a lane the PLATFORM minted for its own work cannot contain a
    # fact about the user, by construction. Measured 2026-08-25 before the table
    # was wiped: 4,480 of 5,212 staged facts (85%) were the incident RCA's own
    # sub-agent prompts ("You are the VERIFIER owl in a fixed-stage incident
    # root-cause analysis..."), because a sub-agent's `input_text` IS its prompt
    # and RCA runs through this same pipeline. One of them was stored 1,505
    # times, which is also most of the table's 66% duplicate rate — three prompts
    # recur on every incident, forever.
    #
    # `is_machine_lane` already existed for exactly this, docstring and all
    # ("lanes that cannot contain a user fact by construction"), with zero
    # callers. Keyed on state.session_key, NOT on the scope key passed below:
    # owner_scope_key is `identity_key or session_key`, so the store cannot tell
    # a machine lane from a person once an identity resolves.
    #
    # The TRANSCRIPT above is deliberately still written — the turn happened and
    # remains inspectable; what is refused is filing it as durable knowledge.
    if is_machine_lane(state.session_key):
        log.memory.info(
            "[pipeline] persist_turn: machine lane — transcript kept, fact NOT staged",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "session_key": state.session_key,
                "content_len": len(content),
            }},
        )
        return

    # 3b. STEP — best-effort store (B5: never raise; never block delivery).
    try:
        # Filed under the OWNER, not the lane: knowledge is about a person, not
        # about which owl happened to hear it. This was the one durable-knowledge
        # site still using the raw lane — see owner_scope_key for why that mattered.
        await bridge.store(content, owner_scope_key(state), trust=trust_override)
    except Exception as exc:  # B5 — memory persistence must not break the turn
        log.memory.warning(
            "[pipeline] persist_turn: store failed — skipping",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id, "session_key": state.session_key}},
        )
        return
    # 4. EXIT
    log.memory.debug(
        "[pipeline] persist_turn: exit",
        extra={"_fields": {"trace_id": state.trace_id, "floored": floored}},
    )


async def _record_transcript(state: PipelineState, floored: bool,
                             assistant_text: str) -> None:
    """Write the durable TRANSCRIPT — what was SAID, not what was learned.

    Deliberately independent of the staged-fact store above: that path returns
    early when it fails, and coupling the transcript to it would mean one
    memory-bridge hiccup silently losing the conversation record too. Six readers
    (owl DNA evolution, fact extraction, session search, transcripts, session
    access, cron owner lookup) query these tables and had been getting an empty
    set because nothing ever wrote them.

    Best-effort, like everything else on this path: a transcript failure must
    never cost the user their reply.
    """
    # getattr, matching how this module already reads sticky_route_cache and
    # retry_queue_store: a caller may inject a narrower services object than the
    # full StepServices, and a transcript is never worth an AttributeError.
    db_pool = getattr(get_services(), "db_pool", None)
    if db_pool is not None:
        try:
            from stackowl.memory.transcript_store import TranscriptStore

            await TranscriptStore(db_pool).record_turn(
                session_key=state.session_key,
                conversation_id=state.conversation_id,
                owl_name=state.owl_name,
                user_text=state.input_text,
                # None on a floored turn — the same honesty rule the staged fact
                # above follows: never record a draft the turn did not deliver.
                assistant_text=None if floored else assistant_text,
                trace_id=state.trace_id,
            )
        except Exception as exc:
            log.memory.warning(
                "[pipeline] persist_turn: transcript write failed — turn unaffected",
                exc_info=exc,
                extra={"_fields": {"trace_id": state.trace_id,
                                   "conversation_id": state.conversation_id}},
            )

    await _count_completed_turn(state, floored)

    # 4. EXIT
    log.memory.debug(
        "[pipeline] persist_turn: exit",
        extra={"_fields": {"trace_id": state.trace_id, "floored": floored}},
    )


async def _count_completed_turn(state: PipelineState, floored: bool) -> None:
    """Record that this lane answered (D01.7 part 4).

    A FLOORED turn is not a completed one. That is the whole point of keeping this
    separate from ``message_count``: a lane taking messages and completing nothing
    is a lane that is failing, and the gap between the two counters is the only
    place that shows. Counting a floored turn here would erase the signal.

    Background work has no incarnation and no lane, so this returns early rather
    than asking the store about a conversation nobody had.
    """
    if floored or not state.conversation_id:
        return
    # getattr for the same reason the transcript above uses it: a caller may
    # inject a narrower services object, and a counter is never worth an
    # AttributeError on the live turn path.
    store = getattr(get_services(), "session_store", None)
    if store is None:
        return
    try:
        await store.record_completed_turn(state.session_key)
    except Exception as exc:
        log.memory.warning(
            "[pipeline] persist_turn: completed-turn count failed — turn unaffected",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id,
                               "session_key": state.session_key}},
        )


