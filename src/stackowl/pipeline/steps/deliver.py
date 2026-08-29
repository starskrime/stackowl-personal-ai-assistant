"""Pipeline step 8: deliver — write response chunks to the StreamRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services, owner_scope_key
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.services import StepServices


async def run(state: PipelineState) -> PipelineState:
    """Write accumulated response chunks to the session's StreamWriter.

    Retrieves the StreamRegistry from pipeline services context.
    Discards gracefully if no writer is registered — never raises.
    """
    # A delegated sub-pipeline (delegation_depth>0) shares the parent's session_key
    # but has NO user stream of its own — its result returns to the parent via the
    # A2A response (final_state.responses), not the user's StreamWriter. Delivering
    # here would write the child's raw text to the PARENT's stream and close it,
    # losing the parent's (footered) answer. Skip delivery for delegated children.
    if state.delegation_depth > 0:
        log.gateway.debug(
            "[pipeline] deliver: delegated sub-pipeline — skip user-stream delivery",
            extra={"_fields": {"session_key": state.session_key,
                               "delegation_depth": state.delegation_depth}},
        )
        return state

    # A non-interactive producer (a scheduler handler — goal_execution,
    # website_watch) runs the full pipeline but OWNS delivery itself via the
    # durable seam (ProactiveJobDeliverer, addressed from jobs.target_*). Such a
    # turn has no live user stream, so the deliver step must NOT also attempt a
    # send (the stream-miss proactive fallback would otherwise fire a SECOND,
    # unledgered copy). defer_delivery makes deliver a no-op for that turn; the
    # handler delivers exactly-once after the pipeline returns.
    if state.defer_delivery:
        log.gateway.debug(
            "[pipeline] deliver: defer_delivery — producer owns delivery, skipping",
            extra={"_fields": {"session_key": state.session_key}},
        )
        return state

    services = get_services()
    # Enforce the owner's stored OutputStyle (markdown/links/tables/emoji/length)
    # at this single channel-agnostic seam, BEFORE both the live-stream write and
    # the proactive fallback — so a recalled preference is an enforced constraint,
    # not a hint the model may ignore. No-op (byte-identical) when no preference set.
    state = await _enforce_output_prefs(state, services)
    registry = services.stream_registry
    log.gateway.info(
        "[pipeline] deliver: entry",
        extra={"_fields": {"session_key": state.session_key, "chunk_count": len(state.responses)}},
    )
    if registry is None:
        log.gateway.warning(
            "[pipeline] deliver: no registry in services — discarding responses",
            extra={"_fields": {"session_key": state.session_key}},
        )
        return state

    # Streams are keyed by request_id (== trace_id) so each concurrent turn owns
    # its own slot. A request_id with no registered writer is a stream-MISS — the
    # live reader is gone (terminal disconnected mid-turn, or the slot was reaped).
    # The output is NEVER rerouted to a default/other slot (the response-side
    # mirror of no-hidden-errors), but STEER-2/F100: a computed top-level answer
    # must not be silently DROPPED. When this turn has a durable channel reply
    # target, fall back to a proactive send via that target so the answer still
    # reaches the user. CLI/single-terminal turns (no reply_target) have no durable
    # destination to push to — there the miss IS terminal and is logged loudly.
    writer = registry.get_writer(state.trace_id)
    if writer is None:
        # THE COMPLETION SEAM RUNS HERE TOO, and it did not until 2026-08-20. This
        # branch is the ONE path that reaches a user whose live stream is gone —
        # which is precisely the recovered turn, the case the loop exists for — and
        # it returned before the seam below. Measured on the live table: two chat
        # rows reading status='completed' with delivered_at NULL, both answered
        # through this branch. Nothing could ever see them: the revival sweep scans
        # only status='failed'. The push's own verdict decides, so a fallback that
        # could not send still records nothing and the row stays open for the loop.
        delivered = await _proactive_fallback(state, services)
        if delivered:
            await _complete_turn(state, services)
        return state

    # REACT-8/F037 — terminal signaling contract. The tool path (and consolidate)
    # build content chunks; ``StreamWriter.close()`` below appends the SINGLE
    # is_final=True sentinel (empty content) that ``StreamReader`` keys on to stop
    # (the reader BREAKS on is_final WITHOUT yielding it). So a content chunk must
    # NEVER carry is_final=True — the reader would swallow its content. The terminal
    # signal for the streaming path is the close() sentinel, not a per-content flag;
    # the cli_adapter/conversation_view is_final checks are satisfied by the adapter's
    # own belt-and-suspenders terminal marker. is_final on a CONTENT chunk is dead for
    # this path by design — kept only for the non-streaming consolidate merge.
    # D01.1 — the next-contact banner is delivered FIRST, as its own chunk, and no
    # longer as system-prompt text (the prompt is frozen per session for invariant
    # I1, and this is volatile by design). is_final stays False per the contract
    # above: close() emits the one terminal sentinel.
    if state.pending_banner:
        await writer.write(
            ResponseChunk(
                content=state.pending_banner + "\n\n",
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
                target=state.reply_target,
            )
        )
        log.gateway.info(
            "[pipeline] deliver: next-contact banner delivered",
            extra={"_fields": {"trace_id": state.trace_id,
                               "session_key": state.session_key,
                               "banner_len": len(state.pending_banner)}},
        )

    for chunk in state.responses:
        if chunk.trace_id and chunk.trace_id != state.trace_id:
            log.gateway.error(
                "[deliver] chunk request_id mismatch — hard drop, never reroute",
                extra={"_fields": {"chunk_request_id": chunk.trace_id, "turn_request_id": state.trace_id}},
            )
            continue
        # Stamp this turn's reply target onto the (frozen) chunk so a fan-out
        # channel (Telegram) routes the output back to ITS OWN chat under
        # concurrency. None for CLI turns — the adapter resolves the destination.
        chunk = chunk.model_copy(update={"target": state.reply_target})
        await writer.write(chunk)
    await writer.close()

    await _complete_turn(state, services)

    log.gateway.info(
        "[pipeline] deliver: exit",
        extra={"_fields": {"session_key": state.session_key, "chunks_written": len(state.responses)}},
    )
    return state


async def _enforce_output_prefs(state: PipelineState, services: StepServices) -> PipelineState:
    """Apply the owner's resolved :class:`OutputStyle` to the response text.

    Loads+merges the per-(owner,channel) prefs UNDER which the structured
    ``output_style`` (markdown/links/tables/emoji/length) is resolved, then
    deterministically enforces + verifies every transform via
    ``OutputStyle.enforce`` — independent of whether the model complied. When
    ``length == "terse"``, additionally runs the real async summariser
    (``_summarize_if_terse``) — the ONE production seam where that upgrade,
    documented but deferred in ``OutputStyle._enforce_length``, actually
    happens — then re-verifies the deterministic fields on the result (a fresh
    LLM summary could reintroduce markdown the style forbids).
    Channel-agnostic and fail-safe (B5): a missing store, no preferences, or any
    error returns ``state`` unchanged — enforcement never crashes delivery. When a
    transform actually rewrites the text, the response chunks are collapsed into
    one transformed content chunk (preserving the turn's owl, target, and floor
    marker). owner_key mirrors classify: ``identity_key`` when set, else
    ``session_key``.
    """
    store = services.preference_store
    if store is None or not state.responses:
        return state
    try:
        from stackowl.memory.preferences import GLOBAL_OWNER_KEY

        owner_key = owner_scope_key(state)
        # Merge the cross-channel GLOBAL prefs UNDER the per-owner prefs so a
        # globally-set preference (e.g. output_tables=off) is enforced on every
        # channel, while a per-owner pref still overrides it. No global pref →
        # byte-identical baseline.
        global_prefs = await store.list_for_owner(GLOBAL_OWNER_KEY)
        owner_prefs = await store.list_for_owner(owner_key)
        prefs = {**global_prefs, **owner_prefs}
        if not prefs:
            return state
        from stackowl.channels._format import resolve_output_style

        resolved_style = resolve_output_style(prefs)
        combined = "".join(c.content for c in state.responses if c.content)
        transformed = resolved_style.enforce(combined)
        if resolved_style.length == "terse":
            transformed = await _summarize_unless_floor(transformed, services, state)
            transformed = resolved_style.verify(transformed)
        if transformed == combined:
            return state  # no preference rewrote anything → byte-identical
        template = state.responses[0]
        chunk = template.model_copy(update={
            "content": transformed,
            "is_final": False,
            "chunk_index": 0,
            "is_floor": any(c.is_floor for c in state.responses),
        })
        log.gateway.info(
            "[pipeline] deliver: output preference enforced",
            extra={"_fields": {"owner_key": owner_key, "before_len": len(combined),
                               "after_len": len(transformed)}},
        )
        return state.evolve(responses=(chunk,))
    except Exception as exc:  # B5 — enforcement must never crash delivery
        log.gateway.error(
            "[pipeline] deliver: output preference enforcement failed — sending as-is",
            exc_info=exc, extra={"_fields": {"session_key": state.session_key}},
        )
        return state


# Below this, a reply already reads as short — skip the summariser call
# entirely rather than risk an LLM "compressing" text that's already terse.
_TERSE_SKIP_BELOW_CHARS = 400


async def _summarize_unless_floor(
    text: str, services: StepServices, state: PipelineState,
) -> str:
    """Compress under ``length=terse`` — but NEVER an honesty floor.

    THE GUARD, and it was earned in production on 2026-08-29T17:25Z.

    ``_floor_chunk``'s docstring promises the floor is "Pure, deterministic, no
    model call" (``delivery_gate.py``). ``synthesize_floor`` renders a template that
    QUOTES THE GOAL back into the message. On that turn the goal was a ``/skill use``
    prompt containing the conditional 'If no skill named "channel-fallback" exists,
    say so plainly' — and this function then handed all 635 characters to a fast-tier
    LLM, which compressed 635 -> 515 by reading that CONDITIONAL as a FACT. The user
    was told the skill did not exist, in the same trace as
    ``skill_view.execute: exit {success: True, skill: channel-fallback}``.

    Worse, the caller re-stamps ``is_floor=True`` on the rewritten chunk, so
    model-authored prose was preserved AS a deterministic floor and every downstream
    reader that trusts that flag was trusting a paraphrase.

    A floor is the platform's promise not to overclaim. It ships byte-for-byte or
    the promise is worthless. Ordinary answers are still compressed — the guard is
    deliberately narrow, because disabling terse mode outright would be a bug in the
    opposite direction.
    """
    if any(c.is_floor for c in state.responses):
        log.gateway.info(
            "[pipeline] deliver: floor NOT summarized — an honesty floor ships verbatim",
            extra={"_fields": {"trace_id": state.trace_id, "chars": len(text)}},
        )
        return text
    return await _summarize_if_terse(text, services, state)


async def _summarize_if_terse(
    text: str, services: StepServices, state: PipelineState,
) -> str:
    """Compress ``text`` via a fast-tier LLM when it's long enough to bother.

    The upgrade path ``OutputStyle._enforce_length`` documents but deliberately
    leaves as a no-op (that method must stay sync/deterministic) — this is
    where length=terse becomes a REAL guarantee instead of an unenforced field.
    Best-effort: returns ``text`` UNCHANGED on any failure (no provider, a
    timeout, an empty reply) — a failed compression must never drop content or
    crash delivery; the alternative (fabricating a truncation) is worse than
    delivering the reply whole.
    """
    if len(text) <= _TERSE_SKIP_BELOW_CHARS:
        return text
    registry = services.provider_registry
    if registry is None:
        return text

    from stackowl.interaction.classifier_base import resolve_fixed_tier, safe_complete
    from stackowl.providers.base import Message

    resolved = resolve_fixed_tier(
        registry, "fast", logger=log.gateway, call_name="length_terse",
    )
    if resolved is None:
        return text
    provider, model = resolved
    system_text = (
        "You compress the assistant's reply to be significantly shorter while "
        "preserving every concrete fact, number, name, and instruction it "
        "contains. Keep the SAME language and the SAME formatting conventions "
        "(markdown, links, emoji) already used — only remove restating, "
        "hedging, and filler prose, never actual content. Reply with ONLY the "
        "compressed text, no preamble, no explanation."
    )
    outcome = await safe_complete(
        provider, model,
        [Message(role="system", content=system_text), Message(role="user", content=text)],
        timeout_s=10.0,
        logger=log.gateway,
        call_name="length_terse",
    )
    if outcome.result is None:  # safe_complete already logged the failure
        log.gateway.warning(
            "[pipeline] deliver: length_terse summarizer failed — delivering full text",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return text
    compressed = (outcome.result.content or "").strip()
    if not compressed:
        log.gateway.warning(
            "[pipeline] deliver: length_terse summarizer returned empty — delivering full text",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return text
    log.gateway.info(
        "[pipeline] deliver: length_terse summarized",
        extra={"_fields": {"trace_id": state.trace_id, "before_len": len(text),
                           "after_len": len(compressed)}},
    )
    return compressed


async def _complete_turn(state: PipelineState, services: StepServices) -> None:
    """THE ONE LOOP's completion seam — the reply has reached the user.

    Bakir, 2026-08-17: *"if it's delivered to me, it means loop is completed."*
    Marking it anywhere earlier would record a success the user never saw, the
    overclaim shape this platform keeps finding. Best-effort by construction:
    ``complete_turn_task`` never raises, so the durable bookkeeping cannot cost a
    delivered turn.

    ONE HELPER BECAUSE THERE ARE TWO DELIVERY PATHS. The live-stream write and the
    stream-miss proactive push both reach the user, and until 2026-08-20 only the
    first proved it. A second inline copy of this call is how the two would drift
    again, so both branches ask this.

    WHICH ID THE PROOF LANDS ON. ``state.task_id`` when the drive carries one,
    ``trace_id`` otherwise. An ordinary chat turn sets no ``task_id`` and its row
    IS keyed by the trace id (``enqueue_turn_task``), so that path is unchanged. A
    RECOVERY drive mints ``trace_id="recover-<12hex>"`` while keeping the real
    ``task_id`` on the state — keying on the trace id there wrote the proof against
    a row that does not exist.
    """
    from stackowl.pipeline.durable.turn_task import complete_turn_task

    await complete_turn_task(
        getattr(services, "durable_task_store", None),
        trace_id=state.task_id or state.trace_id,
        result="".join(c.content for c in state.responses if c.content),
        # The turn's own verification verdict. A reply that APOLOGISES for an effect
        # it measured absent has reached the user without doing the work, and must
        # not record achievement — it goes back on the loop instead.
        state=state,
    )


async def _proactive_fallback(
    state: PipelineState, services: StepServices
) -> str | None:
    """Durably push a top-level turn's answer when its live stream is gone (F100).

    Returns the transport's own status when the answer ACTUALLY reached the user,
    and ``None`` when it did not. The caller turns that into a delivery proof, so
    the verdict is the transport's rather than a second opinion derived beside it —
    two opinions about whether delivery happened is how they drift.

    Called ONLY on a stream-miss for a non-delegated top-level turn (the caller
    already excludes ``delegation_depth>0``). The computed answer is joined and
    handed to the :class:`ProactiveDeliverer`, addressed via THIS turn's own
    ``reply_target`` (the per-turn destination — never the adapter's shared
    mutable ``_last_*``) at ``critical`` urgency so a direct answer is never
    quiet-hours-batched or suppressed away. Self-healing (B5): a missing
    deliverer, a turn with no durable reply target, or a deliverer that raises is
    logged loudly and swallowed — the fallback can never crash the pipeline. When
    no durable push is possible the miss is terminal and is logged as such (the
    response-side mirror of no-hidden-errors).
    """
    deliverer = services.proactive_deliverer
    body = "".join(c.content for c in state.responses if c.content)
    # A CLI / single-terminal turn owns no durable channel target; the adapter
    # resolved the destination, so a missing live writer there is a true terminal
    # miss with nowhere to push. Likewise an empty body or no deliverer.
    if deliverer is None or state.reply_target is None or not body:
        log.gateway.warning(
            "[deliver] stream-miss: no durable fallback available — answer not delivered",
            extra={
                "_fields": {
                    "request_id": state.trace_id,
                    "session_key": state.session_key,
                    "has_deliverer": deliverer is not None,
                    "has_target": state.reply_target is not None,
                    "body_len": len(body),
                }
            },
        )
        return None

    # Import locally so the typing-only services import stays light and there is
    # no import cycle at module load (notifications imports pipeline types).
    from stackowl.notifications.router import Notification

    note = Notification(
        message=body,
        urgency="critical",  # a direct answer must not be batched/suppressed away
        category="turn_answer",
        channel_name=state.channel,
        target=state.reply_target,
    )
    try:
        status = await deliverer.deliver(note)
    except Exception as exc:  # B5 — the fallback must never crash the pipeline.
        log.gateway.error(
            "[deliver] stream-miss: proactive fallback raised — answer not delivered",
            exc_info=exc,
            extra={"_fields": {"request_id": state.trace_id, "session_key": state.session_key}},
        )
        return None
    # THE SAME ROLLUP THE SCHEDULED PATH ALREADY USES. `agent_task` owns the set of
    # statuses that mean the outcome genuinely arrived; asking it rather than
    # re-listing them here keeps ONE source for the rule (a "failed" transport is a
    # miss, and "suppressed" is a decision about delivery rather than a failure of
    # it). Anything outside the set leaves the row open for the loop.
    from stackowl.pipeline.durable.agent_task import DELIVERED_STATUSES

    arrived = str(status) in DELIVERED_STATUSES
    fields = {
        "request_id": state.trace_id,
        "session_key": state.session_key,
        "channel": state.channel,
        "status": status,
        "body_len": len(body),
    }
    if not arrived:
        # THIS USED TO CLAIM DELIVERY REGARDLESS OF THE STATUS IT PRINTED. The
        # deliverer returns "failed" when transport could not complete, and the
        # line still read "answer delivered via proactive fallback" — a log that
        # contradicts its own field. Nobody grepping for a lost answer would have
        # found it.
        log.gateway.error(
            "[deliver] stream-miss: proactive fallback did NOT deliver — the "
            "answer has not reached the user and the task stays open",
            extra={"_fields": fields},
        )
        return None
    log.gateway.warning(
        "[deliver] stream-miss: live reader gone — answer delivered via proactive fallback",
        extra={"_fields": fields},
    )
    return str(status)
