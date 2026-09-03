"""The runner the ONE loop dispatches to — translation, not a second engine.

Slice 4 of Bakir's architecture. The store owns state, the loop owns pacing and
concurrency, and neither knows how to actually ANSWER anything. This closes that
gap in the only way CLAUDE.md permits: by delegating to the engine that already
does the work.

``RetryActuator.attempt_retry`` re-runs a floored turn's goal through the real
backend and delivers the answer to the channel it came from, steering away from
capabilities that already failed. That is precisely what a recovered task needs.
Writing a second re-drive path here would be the duplication the platform rule
exists to prevent — the tree already accumulated four overlapping work engines by
doing exactly that.

So this module is a translator: ``DurableTask`` in, ``RetryAttempt`` out, the
actuator's outcome mapped onto the loop's contract (return the delivered result, or
RAISE so the loop classifies the failure and requeues the row carrying what broke).

THE SAFETY PROPERTY. A chat task only reaches here after its LEASE EXPIRED, which
means the fast path demonstrably did not finish. That is what stops the loop
answering a question the user was already answered — the lease is the handover, and
it is why a turn row is born ``running`` rather than ``pending``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import Awaitable, Callable

    from stackowl.pipeline.durable.task import DurableTask


def actuator_row_for(task: Any) -> Any:
    """The shape RetryActuator re-drives, built from a `tasks` row.

    ONE BUILDER, TWO CALLERS. The loop uses it to re-drive a task it recovered;
    triage uses it when the OPERATOR says "try again" about a task the loop has
    stopped trying. Two spellings of "a task as something the actuator can run"
    would drift, and the field that would drift first is
    ``banned_capabilities`` — the learning the loop paid attempts to acquire,
    whose loss sends the retry back down a route already proven dead.
    """
    from stackowl.pipeline.retry_attempt import RetryAttempt

    return RetryAttempt(
        id=task.task_id,
        trace_id=task.task_id,
        session_key=task.session_key or "",
        goal=(task.goal or "").strip(),
        banned_capabilities=list(task.banned_capabilities),
        attempt_count=task.attempt_count,
        status="pending",
        last_error=task.last_error,
        channel=task.channel or "telegram",
        channel_chat_id=_chat_id_of(task.destination),
    )


def _chat_id_of(destination: str | None) -> str | None:
    """``telegram:72055773`` -> ``72055773``. None for a channel-only destination
    such as ``cli``, which addresses its single terminal implicitly."""
    if not destination or ":" not in destination:
        return None
    return destination.split(":", 1)[1] or None


def build_task_runner(actuator: Any) -> Callable[[DurableTask], Awaitable[str]]:
    """Return the coroutine the loop calls for one claimed task.

    Takes the actuator rather than importing it so the runner can be driven by a
    double in tests without a backend, a channel registry or a database — and so
    the wiring stays visible at assembly instead of hidden behind an import.
    """

    async def _run(task: DurableTask) -> str:
        if actuator is None:
            # Refuse loudly. Returning quietly would have the loop mark every
            # recovered task delivered while doing nothing — silent, and worse than
            # not running at all.
            raise RuntimeError(
                "no retry actuator wired — the loop cannot re-drive this task"
            )
        goal = (task.goal or "").strip()
        if not goal:
            raise RuntimeError(f"task {task.task_id} has no goal to re-drive")

        row = actuator_row_for(task)
        log.tasks.info(
            "[loop] re-driving a recovered task through the retry actuator",
            extra={"_fields": {"task_id": task.task_id, "attempt": task.attempt_count,
                               "banned": list(task.banned_capabilities),
                               "destination": task.destination}},
        )
        outcome = await actuator.attempt_retry(row)
        status = str(getattr(outcome, "status", "") or "")
        if status != "completed":
            # NOT a quiet return. The loop marks a task delivered on a non-empty
            # result, so reporting success here when nothing reached the user would
            # import the overclaim shape straight into the loop.
            #
            # THE LEARNING RIDES THE EXCEPTION. A raise is the only channel this
            # runner has back to the loop, so what the attempt burned is attached
            # to it rather than discarded with the outcome object. Without this the
            # loop reads an empty tuple every time and `banned_capabilities` never
            # accumulates — which is precisely how 8b7c4029 failed identically 74
            # times against a ceiling of 30.
            # THE WORK'S REASON WINS over the machinery's. `loop.py` stores this
            # exception's text as the task's last_error, and `_augment_goal` shows
            # that text to the NEXT attempt as "what happened last time". Reporting
            # the actuator's own status there made every retry read "retry did not
            # deliver (actuator reported 'pending')" — true, and useless, and
            # measured on every one of the five tasks that carried it.
            reason = str(getattr(outcome, "reason", "") or "").strip()
            err = RuntimeError(
                reason or f"retry did not deliver (actuator reported {status!r})"
            )
            err.banned_capabilities = tuple(  # type: ignore[attr-defined]
                getattr(outcome, "banned", ()) or ()
            )
            raise err
        return f"re-driven and delivered after {task.attempt_count} prior attempt(s)"

    return _run
