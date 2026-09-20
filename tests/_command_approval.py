"""Test-only helper: approve a PARKED command and run it to completion.

Story 4.7 declares ``scheduling.delete_job``/``run_now_job``/``set_objective``
IRREVERSIBLE, so the action-policy gate demands step-up for EVERY requester
kind, including the owner (Design Notes) — an ordinary tool call through
these now parks awaiting approval rather than completing inline. A test that
exercises the REAL tool path end-to-end (not the handler directly) needs a
way to simulate the owner's step-up tap without building the full
Needs-you/Telegram approval UI; this is that one shared helper, mirroring
what ``pipeline/durable/command_resume_sweep.py`` does in production
(``resume_command_after_answer`` then re-run through the one door).
"""

from __future__ import annotations

from stackowl.commands.spec.context import CommandOutcome
from stackowl.commands.spec.execute import execute_command_task
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices, reset_services, set_services


async def approve_one_parked_command(db: DbPool, command_type: str) -> CommandOutcome:
    """Find the single PARKED row of *command_type*, approve it (mirrors the
    owner's step-up tap), and run it through :func:`execute_command_task` to
    completion. Asserts exactly one parked row exists — a test calling this
    should know precisely which command it is approving.

    Binds ``get_services().db_pool`` to *db* for the duration of the handler
    call and restores whatever was bound before — every real (non-stub)
    command handler resolves its db this way (``CommandHandler``'s shape is
    fixed at ``(payload, context) -> CommandOutcome``, no bespoke db
    parameter), and by the time a test calls this helper it has typically
    already reset its own services binding.
    """
    rows = await db.fetch_all(
        "SELECT task_id FROM tasks WHERE kind = 'command' AND command_type = ? "
        "AND status = 'parked'",
        (command_type,),
    )
    assert len(rows) == 1, (
        f"expected exactly one parked {command_type!r} command, found {len(rows)}"
    )
    task_id = rows[0]["task_id"]
    store = DurableTaskStore(db)
    await store.resume_command_after_answer(task_id, approved=True)
    task = await store.get(task_id)
    token = set_services(StepServices(db_pool=db))
    try:
        return await execute_command_task(task)
    finally:
        reset_services(token)
