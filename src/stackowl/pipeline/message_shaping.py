"""Message-array shaping applied before a provider call.

Home for shaping rules that belong to NO single pipeline step. ``classify`` and
``execute`` both need them, and a helper living in whichever step happened to need
it first is how D02.1's stated property — "cross_step_imports: ZERO ... they
communicate ONLY through an immutable PipelineState" — quietly became false
(measured 2026-08-30: execute imported merge_consecutive_roles from classify).

Pure functions over ``list[Message]`` only: no state, no I/O, no step imports, so
nothing here can recreate that coupling.

PLACEMENT, stated rather than assumed, because this repo requires placement to be
argued. Three homes were considered:
  * ``providers/base.py`` — the rule enforces a PROVIDER contract ("strict
    providers reject two consecutive turns of the same role"), which is the
    strongest argument against this file. Rejected because base.py is already the
    largest module in that package and both callers are pipeline steps.
  * ``pipeline/persistence.py`` — already touches message shaping, but its subject
    is durability, not provider-array validity. Rejected as a coincidence of shape.
  * here — a named module whose whole subject is the shaping. Chosen; it is one
    small file and trivially reversible if the provider-contract argument wins
    later.
"""

from __future__ import annotations

from stackowl.providers.base import Message


def merge_consecutive_roles(messages: list[Message]) -> list[Message]:
    """Collapse runs of same-role messages so the array strictly alternates.

    D01.5. Strict providers reject a messages array with two consecutive turns of
    the same role outright. The live provider tolerates it, which is the only
    reason this has never surfaced as an error — so the RISK is latent while the
    VIOLATION is not.

    MEASURED 2026-08-26 by running the real parser over the real stored rows: 2 of
    4 live conversations violate alternation, and one of them is the operator's own
    lane, whose history opens ``A A A A`` — four consecutive assistant turns.

    WHY IT HAPPENS: a stored row is ``"User: X\n\nAssistant: Y"``, and a scheduled
    job writes one with an EMPTY user half (a daily digest nobody asked for in
    words). ``_parse_turns_to_messages`` skips empty halves — correctly, because a
    blank-content turn is itself rejected — so such a row contributes a bare
    assistant message with no user turn before it. Several in a row produce the
    run above. ``_dedup_assistant_history`` can leave the mirror image (``U U``)
    by removing an assistant turn from between two user turns.

    MERGE, NOT SYNTHESISE. Joining the run's contents keeps every word the model
    and the user actually produced and invents nothing. The alternative — slipping
    a placeholder user turn between two assistant turns — puts words in the user's
    mouth, and a fabricated turn in the history is a worse defect than the one it
    repairs.
    """
    out: list[Message] = []
    for msg in messages:
        if out and out[-1].role == msg.role:
            out[-1] = Message(role=msg.role, content=f"{out[-1].content}\n\n{msg.content}")
            continue
        out.append(msg)
    return out
