"""Static schema/description for ``mixture_of_agents`` (B2 split from the tool).

The model-facing description HARD-GATES use (ADR-11): MoA fans one question across
N distinct models then synthesizes — expensive, so it must be reserved for a single
genuinely hard/contested question, never routine work. Kept as module constants so
``mixture_of_agents.py`` stays under the B2 line cap.
"""

from __future__ import annotations

MIXTURE_OF_AGENTS_DESCRIPTION = (
    "Consult SEVERAL different models independently on ONE genuinely hard or "
    "contested question, then return a single synthesized verdict with dissent "
    "preserved. This fires N separate model calls and is EXPENSIVE — use it ONLY "
    "for a single high-stakes question where independent expert opinions add real "
    "value (a thorny trade-off, a contested design/decision, a claim you want "
    "cross-checked). Provide 'question' (the one self-contained question) and "
    "optionally 'max_agents' (cap how many models to consult). A result with "
    "status 'insufficient_roster' means fewer than two distinct healthy models "
    "are available — answer the question directly yourself instead. The result "
    "notes how many models were consulted and flags 'degraded_ensemble' if some "
    "failed. LANE: one hard/contested question worth several independent opinions. "
    "ANTI-LANE: do NOT use for routine questions, multi-step tasks, or anything a "
    "single answer handles — that wastes N model calls."
)

MIXTURE_OF_AGENTS_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The one self-contained, hard/contested question to put to several models.",
        },
        "max_agents": {
            "type": "integer",
            "description": "Optional cap on how many distinct models to consult (default: all healthy).",
        },
    },
    "required": ["question"],
}
