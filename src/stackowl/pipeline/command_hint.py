"""surface_command_hint — pre-delivery, additive natural-language → command hint.

Issue 3 (WS-D): when a natural-language turn would have been served faster by a
slash command, additively append a MARKED, non-intrusive hint to the owl's reply
("☆ tip — you can also do this directly: /memory forget ..."). The owl still
answers normally; the hint NEVER auto-runs. This also consumes the previously
dead ``RouteDecision.suggestion`` channel (a fuzzy owl-routing correction).

Honesty spine:
* Gated by ``ui.command_hints`` — OFF by default, so this surfacer is a no-op and
  the response is byte-identical to the baseline.
* Only annotates a REAL (non-floor) answer — never decorates a failed/honest-floor
  turn, never replaces content.
* High score threshold so a genuine conversation is not hijacked by a weak match.
* Never raises (B5) — a hint can never break delivery.

Runs once per turn, before deliver, in BOTH backends (sibling to
``surface_applied_lessons`` / ``surface_critical_failure``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.pipeline.services import StepServices

# Only suggest a command when the resolver is highly confident — a conservative
# floor so everyday conversation is never hijacked. Score is in [0, 1].
_HINT_SCORE_FLOOR = 0.45
_MAX_HINT_LINES = 2


async def surface_command_hint(
    state: PipelineState, services: StepServices
) -> PipelineState:
    """Append a marked command hint and/or a routing-correction notice. No-op
    when the feature is off, the turn is not a real interactive answer, or no
    high-confidence command matches."""
    try:
        settings = services.settings
        if settings is None or not settings.ui.command_hints:
            return state  # feature off → byte-identical baseline
        # Only ever annotate a real, user-facing answer — never a floor/failure.
        has_real_answer = any(
            c.content.strip() and not c.is_floor for c in state.responses
        )
        if not has_real_answer:
            return state

        lines: list[str] = []

        # (a) Consume the dead RouteDecision.suggestion channel: surface a fuzzy
        # owl-routing correction the scanner inferred for this turn.
        if state.route_suggestion:
            lines.append(state.route_suggestion.strip())

        # (b) Resolver-based command hint for a genuine natural-language turn.
        resolver = services.command_hint_resolver
        query = state.input_text.strip()
        is_prose = bool(query) and not query.startswith(("/", "@"))
        if (
            resolver is not None
            and state.interactive
            and state.delegation_depth == 0
            and is_prose
        ):
            candidates = await resolver.resolve(query, limit=1)
            if candidates and candidates[0].score >= _HINT_SCORE_FLOOR:
                top = candidates[0]
                summary = f" — {top.summary}" if top.summary else ""
                lines.append(
                    f"☆ tip: you can also do this directly with "
                    f"`{top.invocation}`{summary}"
                )

        if not lines:
            return state

        base_index = len(state.responses)
        new_chunks = [
            ResponseChunk(
                content=f"\n\n{line}",
                is_final=False,
                chunk_index=base_index + offset,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            for offset, line in enumerate(lines[:_MAX_HINT_LINES])
        ]
        log.engine.info(
            "[command_hint] surfaced command hint(s)",
            extra={"_fields": {"trace_id": state.trace_id, "n": len(new_chunks)}},
        )
        return state.evolve(responses=(*state.responses, *new_chunks))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[command_hint] surfacing failed — leaving response untouched",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
