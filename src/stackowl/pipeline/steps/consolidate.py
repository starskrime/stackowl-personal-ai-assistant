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


def _qualifies_for_rating(chunk: ResponseChunk) -> bool:
    return not chunk.is_floor and len(chunk.content) >= _MIN_RATEABLE_LENGTH


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
        # non-empty error body must NEVER be delivered as the answer.
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
    # Task 5 — attach an approach-rating like/dislike keyboard to a qualifying
    # final answer (substantial, non-floor). Best-effort: a tracker failure must
    # never break delivery of the answer itself.
    if out_state.responses:
        last = out_state.responses[-1]
        if _qualifies_for_rating(last):
            services = get_services()
            tracker = services.approach_rating_tracker
            if tracker is not None:
                try:
                    tracker.record_pending(trace_id=out_state.trace_id)
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
                    # leaks a trace_id in the tracker's _pending dict forever
                    # (otherwise only a user tap would ever clear it).
                    tracker.clear(trace_id=out_state.trace_id)
    log.engine.info(
        "[pipeline] consolidate: exit",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return out_state
