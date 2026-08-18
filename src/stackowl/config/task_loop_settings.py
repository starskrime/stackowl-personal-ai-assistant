"""TaskLoopSettings — the ONE loop's tunables (Bakir's architecture, 2026-08-17).

Every value here is a number Bakir named, and it lives in config for the reason he
gave: *"each task we may have around thirty limit to try. And this thirty can be in
configuration."* A constant compiled into the loop cannot be changed without a
deploy, and a loop that runs unattended is exactly the thing an operator needs to
be able to slow down, tighten, or stop.

Its own file rather than another block inside ``settings.py`` (already ~1,000
lines) — same shape as ``notification_settings.py`` and ``ui_settings.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TaskLoopSettings(BaseModel):
    """How the one loop paces itself, retries, and gives up."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "Master switch for the durable task loop. Off means nothing claims "
            "pending tasks — rows still accumulate, so turning it back on resumes "
            "rather than loses work."
        ),
    )
    tick_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "How often the loop looks for claimable work. Bakir asked for five "
            "seconds. This is the SAFETY NET rather than the only trigger: an "
            "enqueue can wake the loop immediately, and the tick is what catches "
            "expired leases, backed-off retries and anything a missed signal "
            "dropped."
        ),
    )
    max_parallel: int = Field(
        default=5,
        ge=1,
        description=(
            "How many tasks run CONCURRENTLY. Bakir: 'if in table we have five "
            "pending, five loops parallel... there is no ordering.' A ceiling "
            "rather than a promise — it bounds how much of this box a runaway "
            "backlog can take."
        ),
    )
    default_max_attempts: int = Field(
        default=30,
        ge=1,
        description=(
            "Attempts before a task dead-letters, when its row does not say "
            "otherwise. Bakir's thirty. Per-task rows may override it, so a cheap "
            "certainty and an expensive maybe are not forced to share a budget."
        ),
    )
    lease_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "How long a claim is good for. Long enough that a slow but live task "
            "is not stolen mid-flight; short enough that a crashed worker's row "
            "returns quickly. Exceeding it counts an attempt, so a task that "
            "reliably kills its worker still reaches the ceiling."
        ),
    )
    prune_completed_after_days: int = Field(
        default=1,
        ge=1,
        description=(
            "Bakir: 'delete completed jobs older than one day.' DELIVERED rows "
            "only — a dead_letter is never pruned, because it is the one record "
            "of work that failed for good and is precisely what an operator needs "
            "to see. The learning corpus lives in task_outcomes and is untouched."
        ),
    )
    permanent_failure_classes: tuple[str, ...] = Field(
        default=("permanent", "auth", "not_found", "refused"),
        description=(
            "Failure classes that stop a task IMMEDIATELY instead of spending its "
            "whole attempt budget. A missing API key fails identically every time, "
            "so thirty attempts is thirty guaranteed-wasted model calls — seconds "
            "of latency each on this hardware. Configurable because which failures "
            "are truly permanent is deployment-specific: what is unrecoverable "
            "behind one gateway may be a transient blip behind another."
        ),
    )
    produce_replies: bool = Field(
        default=False,
        description=(
            "The LOOP produces the chat reply, not merely recovers one — Bakir's "
            "design in full: 'the loop should go understand, find the answer, "
            "return back answer to the Telegram.'\n\n"
            "TURNED BACK OFF 2026-08-18 after Bakir used it, and the reason is "
            "worth keeping: loop production WORKS (measured — claim to delivered "
            "in 9s and 23s on real turns), but it loses the instant "
            "acknowledgement that the fast path sends and then REPLACES IN PLACE "
            "with the real answer. On a platform where a turn takes 9-30s, that "
            "ack is what tells the user their message was received at all; without "
            "it the wait reads as nothing happening.\n\n"
            "This goes back ON once the loop path sends its own ack and edits it "
            "in place — RetryActuator._deliver_success ALREADY edits a message "
            "when it has a channel_message_id, so the missing piece is creating "
            "one, not new delivery machinery. Until then the fast path produces "
            "and the loop owns recovery; the durable row, the delivery rule and "
            "the retry ladder are identical either way."
        ),
    )
    escalate_dead_letters: bool = Field(
        default=True,
        description=(
            "Tell the operator when a task stops for good. ON by default because "
            "a loop that abandons work silently is worse than one that fails "
            "loudly — the whole point of a durable queue is that nothing vanishes "
            "unnoticed."
        ),
    )
