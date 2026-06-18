"""Static schema/description text for ``delegate_task`` (B2 split from the tool).

The model-facing description and JSON-Schema parameter block are kept here as
module constants so ``delegate_task.py`` stays under the B2 line cap. No logic —
just the declarative contract the tool exposes through its manifest.
"""

from __future__ import annotations

DELEGATE_TASK_DESCRIPTION = (
    "Hand a focused sub-task to a SPECIALIST owl and wait for its result. "
    "Provide 'goal' (the self-contained sub-task) and optionally 'to_owl' "
    "(a specific specialist by name), 'role' (pick a specialist by role when "
    "you don't know the name), and 'context' (extra background). The "
    "specialist runs its own sub-pipeline and returns its answer with a "
    "provenance footer noting it was a delegated run. A result with status "
    "'timeout_or_empty' means the specialist produced nothing — do the work "
    "yourself or tell the user, do NOT invent its answer. A status 'refused' "
    "means a safety limit (delegation depth or width) was hit — handle the "
    "sub-task yourself. LANE: offloading a focused chunk to a better-suited "
    "owl. ANTI-LANE: do NOT delegate the user's whole request, and do NOT "
    "chain delegations deeply (there is a hard depth cap). "
    "If a result status is 'cycle', 'target_not_found', 'child_error', 'timeout', or 'empty', "
    "the delegation FAILED — do NOT call delegate_task again for this request; "
    "do the work yourself or tell the user plainly. "
    "Prefer giving 'to_owl' explicitly."
)

DELEGATE_TASK_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "goal": {
            "type": "string",
            "description": "The self-contained sub-task for the specialist to perform.",
        },
        "to_owl": {
            "type": "string",
            "description": "Target specialist owl by name (optional — else resolved by role/default).",
        },
        "role": {
            "type": "string",
            "description": "Pick a specialist by role when the exact name is unknown (optional).",
        },
        "context": {
            "type": "string",
            "description": "Extra background the specialist needs to do the sub-task (optional).",
        },
    },
    "required": ["goal"],
}
