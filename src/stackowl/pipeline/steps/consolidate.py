"""Pipeline step 6: consolidate — merge owl outputs and tool results.

F088: persistence was RELOCATED out of this step into
``stackowl.pipeline.turn_persist.persist_turn``, which the backends call AFTER
the honest floor band so the dressed-up pre-floor draft is never stored/promoted.
consolidate now only merges tool output and carries the SP-2 trust decision forward.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

# Approach-rating keyboard is only attached to answers substantial enough that a
# like/dislike vote is meaningful — a one-line reply isn't worth rating.
_MIN_RATEABLE_LENGTH = 200

# Epic 3 Task 2 — token-usage line appended to the final answer. Kept as a
# module constant so the emoji/format is defined in exactly one place.
_TOKEN_LINE_TEMPLATE = "\n\n\U0001F522 {input_tokens:,} in / {output_tokens:,} out"


def _qualifies_for_rating(chunk: ResponseChunk) -> bool:
    return not chunk.is_floor and len(chunk.content) >= _MIN_RATEABLE_LENGTH


async def _append_token_line(out_state: PipelineState) -> PipelineState:
    """Attach a "N in / M out" token-usage line as the final answer's ``display_suffix``.

    Display-only chrome: it is stored on ``display_suffix``, NEVER folded into
    ``content`` — ``content`` is what flows into ``persist_turn`` and the memory
    bridge, so a token-count footer living in ``content`` would be recalled and
    re-injected as model context on every future turn (a real risk of the model
    starting to mimic/hallucinate token-footers in its own output). See
    ``ResponseChunk.display_suffix``.

    Gated to the telegram channel: the spec ("every final Telegram answer")
    scopes this to Telegram; other channels never render it. Best-effort: a
    cost_tracker lookup failure must never break delivery of the answer itself.
    Must run BEFORE Task 5's approach-rating keyboard-attach block (below) so
    that block's stored "original text" for edit-in-place reconstruction can
    combine ``content`` with this ``display_suffix``.
    """
    if out_state.channel != "telegram":
        return out_state
    if not out_state.responses:
        return out_state
    last = out_state.responses[-1]
    if last.is_floor:
        return out_state
    services = get_services()
    cost_tracker = getattr(services, "cost_tracker", None)
    if cost_tracker is None:
        return out_state
    try:
        totals = await cost_tracker.get_turn_token_totals(out_state.trace_id)
    except Exception as exc:  # token display must never break delivery
        log.engine.error(
            "[pipeline] consolidate: token totals lookup failed",
            exc_info=exc,
            extra={"_fields": {"trace_id": out_state.trace_id}},
        )
        return out_state
    if totals is None:
        return out_state
    input_tokens, output_tokens = totals
    suffix = _TOKEN_LINE_TEMPLATE.format(input_tokens=input_tokens, output_tokens=output_tokens)
    updated_chunk = last.model_copy(update={"display_suffix": suffix})
    return out_state.evolve(responses=(*out_state.responses[:-1], updated_chunk))


async def run(state: PipelineState) -> PipelineState:
    log.engine.info(
        "[pipeline] consolidate: entry",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "responses": len(state.responses),
            "tool_calls": len(state.tool_calls),
        }},
    )
    out_state = state
    # Detect the merge condition BEFORE mutating out_state so the flag is
    # computed from the SAME condition as the branch below.
    merged_external = bool(state.tool_calls and not state.responses)
    # Merge tool results into responses when tool_calls produced content but responses is empty.
    if merged_external:
        # F095 — failure-aware merge: only SUCCESSFUL tool output may become the
        # answer. ``tc.error is None`` is the ONLY typed success signal at the
        # pipeline layer (ToolCall has no `failed` bool; the TOOL_FAILED_MARKER is
        # stripped at the provider seam before results land here). A failed tool's
        # non-empty error body must NEVER be delivered as the answer. This filter
        # is only real because execute.py's _tool_call_from_record() populates
        # ``error`` from the provider's own computed failure flag — previously
        # every ToolCall construction site hardcoded error=None, making this
        # filter a silent no-op that let failed tool output through as "the
        # answer" whenever this merge branch fired.
        combined = "\n\n".join(
            tc.result for tc in state.tool_calls if tc.result and tc.error is None
        )
        if combined:
            # REACT-8/F037 — is_final MUST be False on a CONTENT chunk: StreamReader
            # BREAKS on is_final WITHOUT yielding it, so an is_final=True merged chunk
            # would be SWALLOWED (the user would lose the merged tool output). The
            # terminal signal for the streaming path is deliver's close() sentinel,
            # not a per-content flag. (Latent bug fixed: this chunk was is_final=True.)
            chunk = ResponseChunk(
                content=combined,
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            log.engine.info(
                "[pipeline] consolidate: merged tool results",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            out_state = state.evolve(responses=(chunk,))
        else:
            # tool_calls present but all results were empty — no external content merged.
            merged_external = False
    # SP-2 — carry the merge/trust decision forward on state. Computed HERE (where
    # responses was still empty) and stamped so the post-floor persist_turn (F088)
    # reads it instead of recomputing from post-floor responses (trust-laundering guard).
    out_state = out_state.evolve(merged_external=merged_external)
    # Epic 3 Task 2 — attach the token-usage display_suffix BEFORE Task 5's
    # rating-keyboard attach block below, so that block's "original text" for
    # edit-in-place reconstruction can combine content + display_suffix.
    out_state = await _append_token_line(out_state)
    # Task 5 — attach an approach-rating like/dislike keyboard to a qualifying
    # final answer (substantial, non-floor). Best-effort: a tracker failure must
    # never break delivery of the answer itself. Gated to telegram: the tracker
    # is only ever drained by a Telegram callback tap (tracker.clear()), so a
    # non-Telegram turn recording a pending vote here would leak forever — no
    # other channel can ever clear it.
    if out_state.responses and out_state.channel == "telegram":
        last = out_state.responses[-1]
        if _qualifies_for_rating(last):
            services = get_services()
            tracker = services.approach_rating_tracker
            if tracker is not None:
                try:
                    # Combine content + display_suffix: the token line no longer
                    # lives in `content` (see _append_token_line), so a vote-tap
                    # reconstruction from `content` alone would silently drop the
                    # token line the user actually saw.
                    stored_text = last.content + (last.display_suffix or "")
                    await tracker.record_pending(trace_id=out_state.trace_id, text=stored_text)
                    keyboard = tracker.build_keyboard(trace_id=out_state.trace_id)
                    rated_chunk = last.model_copy(update={"raw_keyboard": keyboard})
                    out_state = out_state.evolve(
                        responses=(*out_state.responses[:-1], rated_chunk)
                    )
                except Exception as exc:  # rating attachment must never break delivery
                    log.engine.error(
                        "[pipeline] consolidate: approach-rating keyboard attach failed",
                        exc_info=exc,
                        extra={"_fields": {"trace_id": out_state.trace_id}},
                    )
                    # record_pending may have already landed before a later step
                    # in this try raised — clear it here so a failed attach never
                    # leaks a trace_id in the pending-vote table forever
                    # (otherwise only a user tap would ever clear it).
                    await tracker.clear(trace_id=out_state.trace_id)
    log.engine.info(
        "[pipeline] consolidate: exit",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return out_state
