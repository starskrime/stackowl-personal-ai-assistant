"""``resume_resolved_parked_commands`` -- the durable-state sweep that
resumes a parked COMMAND task once its bound Needs-you item is answered
(Story 4.4, AD-27/AD-28).

WHY A SWEEP, NOT AN EVENT HOOK (Design Notes). ``commands/spec/`` cannot
import ``journal/`` (AD-7), and ``journal/needs_you.resolve()`` cannot import
``commands/spec/``/``pipeline/durable`` without inverting that same boundary.
A periodic seeded job (mirrors ``journal.needs_you.sweep_expired_items`` and
its own seeded handler, ``scheduler/handlers/needs_you_expiry_sweep.py``)
plus one boot-time pass (``startup/orchestrator.py``, right next to
``needs_you.expire_stranded_turn_waiters``'s own boot call) reads ONLY
durable rows (``tasks`` + ``needs_you``) either way -- uniformly correct
whether the answer arrived a second ago or during a restart, with zero
in-memory state to lose. This is what makes "the waiter re-materialises from
durable state instead of expiring" literally true and testable without a
live process restart.

NEVER RAISES OUT OF A CANDIDATE-QUERY OR PER-ROW FAILURE -- mirrors
``needs_you.sweep_expired_items``'s own shape exactly: one row at a time, a
per-row failure is logged and skipped, never fatal to the rest of the pass.
The seeded job calling this must never fail a tick over one bad row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.journal import needs_you
from stackowl.pipeline.durable.store import DurableTaskStore

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from stackowl.db.pool import DbPool

#: Story 4.4's own binary answer vocabulary (I/O matrix: rows literally read
#: ``answer="approved"``/``answer="denied"``) -- no live answering surface
#: ships this story (Boundaries: the Telegram inline-keyboard for THIS item
#: kind is deferred), so this is the ONE place that convention is decided.
#: Anything other than the exact string below is treated as a denial
#: (fail-closed: an ambiguous answer must never be read as approval).
_ANSWER_APPROVED = "approved"


async def resume_resolved_parked_commands(db_pool: DbPool) -> list[str]:
    """Resume every parked COMMAND task whose bound Needs-you item has
    settled -- called by the seeded periodic job
    (``scheduler/handlers/command_resume_sweep.py``) and once at boot
    (``startup/orchestrator.py``).

    Queries ``tasks WHERE kind='command' AND status='parked'`` -- purely
    durable state, no in-memory registry of "what is waiting" anywhere. For
    each candidate, looks up its bound item via
    :func:`~stackowl.journal.needs_you.get_item_by_waiter` and, when it has
    settled, resumes the task through
    :meth:`~stackowl.pipeline.durable.store.DurableTaskStore.
    resume_command_after_answer` -- approved runs the handler on its next
    claim, anything else requeues/dead-letters through the existing ceiling
    rules. A still-open item (nobody has answered yet) is left exactly as
    it is -- not every parked row is due for anything on a given pass.

    Returns the ids of every task actually resumed this pass.
    """
    # 1. ENTRY
    log.tasks.debug(
        "[commands] command_resume_sweep.resume_resolved_parked_commands: entry",
    )
    try:
        candidates = await db_pool.fetch_all(
            "SELECT task_id, owner_id FROM tasks "
            "WHERE kind = 'command' AND status = 'parked'",
            (),
        )
    except Exception as exc:  # noqa: BLE001 -- never raise out of a maintenance sweep
        log.tasks.warning(
            "[commands] command_resume_sweep.resume_resolved_parked_commands: "
            "candidate query failed -- treating this pass as zero candidates",
            exc_info=exc,
        )
        return []

    # 2. DECISION -- one row at a time; a per-row failure is logged and
    # skipped, never fatal to the rest of the pass (mirrors
    # needs_you.sweep_expired_items's own shape).
    resumed: list[str] = []
    for candidate in candidates:
        task_id = str(candidate["task_id"])
        owner_id = str(candidate["owner_id"])
        try:
            item = await needs_you.get_item_by_waiter(
                db_pool, waiter_kind=needs_you.WAITER_KIND_COMMAND, waiter_id=task_id,
            )
            if item is None or item.resolved_cursor is None:
                # No waiter bound yet, or still open — nothing to resume
                # this pass; the row stays parked.
                continue
            # 3. STEP
            store = DurableTaskStore(db_pool, owner_id=owner_id)
            approved = item.answer == _ANSWER_APPROVED
            await store.resume_command_after_answer(
                task_id, approved=approved, reason=item.answer,
            )
            resumed.append(task_id)
        except Exception as exc:  # noqa: BLE001 -- one bad row must not sink the pass
            log.tasks.warning(
                "[commands] command_resume_sweep.resume_resolved_parked_commands: "
                "one row failed to resume -- continuing with the rest of the pass",
                exc_info=exc, extra={"_fields": {"task_id": task_id}},
            )

    # 4. EXIT
    log.tasks.info(
        "[commands] command_resume_sweep.resume_resolved_parked_commands: exit",
        extra={"_fields": {
            "candidate_count": len(candidates), "resumed_count": len(resumed),
        }},
    )
    return resumed
