"""What the retry actuator re-drives: one attempt at a goal, and what it learned.

THIS USED TO BE ``RetryQueueRow`` IN ``memory/retry_queue_store.py`` — "read-side
projection of one retry_queue row". It is not that any more and has not been since
2026-08-28, when commit 49601f50 moved retries onto the ONE loop and removed the
only writer of that table. Every instance the platform builds today is
SYNTHESISED from a `tasks` row by ``actuator_row_for``: there is no retry_queue
row behind it, and there cannot be.

SO IT MOVED OUT OF THE STORE, and the store went. Leaving the shape in a module
named after a retired table is how the next reader concludes the table is live —
the same reasoning that deleted the table itself, applied to the name.

THE FIELDS ARE THE CONTRACT between the loop and the actuator, and one of them is
load-bearing beyond its size: ``banned_capabilities`` is the learning the loop
paid failed attempts to acquire. Dropping it sends the next attempt back down a
route already proven dead — measured on task 8b7c4029, which failed 74 times.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetryAttempt:
    """One goal the platform is about to try again, with its history attached."""

    id: str
    trace_id: str
    session_key: str
    goal: str
    #: What already failed for this goal. The whole reason a retry is better than
    #: a re-ask: the next attempt is constrained rather than blind.
    banned_capabilities: list[str] = field(default_factory=list)
    #: THE OWL THAT RAN THE TURN, carried so the retry RESUMES rather than
    #: re-routes. MEASURED 2026-09-03: a turn that built the `jobmarket` owl
    #: floored after doing the work; the loop recovered it, triage re-routed the
    #: goal through the router, and by then the owl the turn had just CREATED
    #: matched "job market" and answered its own recovery — reporting "that agent
    #: already exists, I'm it" to a user who had asked for it to be built.
    owl_name: str = ""
    attempt_count: int = 0
    status: str = "pending"
    next_retry_at: str = ""
    last_error: str | None = None
    channel: str = "telegram"
    channel_chat_id: str | None = None
    #: Kept, and always None since the ONE-loop move: a `tasks` row carries a
    #: destination but no message id, so a retry now SENDS rather than EDITS.
    #: Retained because the actuator's delivery path still branches on it and
    #: removing the branch is a separate, user-visible change.
    channel_message_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
