"""Pipeline step 6: consolidate — merge owl outputs and tool results.

F088: persistence was RELOCATED out of this step into
``stackowl.pipeline.turn_persist.persist_turn``, which the backends call AFTER
the honest floor band so the dressed-up pre-floor draft is never stored/promoted.
consolidate now only merges tool output and carries the SP-2 trust decision forward.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState


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
        from stackowl.pipeline.streaming import ResponseChunk
        # F095 — failure-aware merge: only SUCCESSFUL tool output may become the
        # answer. ``tc.error is None`` is the ONLY typed success signal at the
        # pipeline layer (ToolCall has no `failed` bool; the TOOL_FAILED_MARKER is
        # stripped at the provider seam before results land here). A failed tool's
        # non-empty error body must NEVER be delivered as the answer.
        combined = "\n\n".join(
            tc.result for tc in state.tool_calls if tc.result and tc.error is None
        )
        if combined:
            chunk = ResponseChunk(
                content=combined,
                is_final=True,
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
    log.engine.info(
        "[pipeline] consolidate: exit",
        extra={"_fields": {"trace_id": state.trace_id}},
    )
    return out_state
