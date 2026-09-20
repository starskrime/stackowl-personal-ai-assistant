"""Story 4.4 -- ``submit_command``'s inline path catches
``CommandNeedsDecisionError`` and parks (the I/O matrix's park/dedupe rows).

Mirrors ``test_submit_command.py``'s own real-SQLite-DbPool style: parking
writes a real ``tasks`` row transition and opens a real ``needs_you`` row
through the actual partial unique index, neither of which a double could
prove.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.commands.spec.submit import submit_command
from stackowl.db.pool import DbPool
from stackowl.journal.needs_you import WAITER_KIND_COMMAND
from stackowl.pipeline.durable.store import DurableTaskStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_TYPE = "widget.needs_a_decision"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    widget_id: str = Field(min_length=1)


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "submit_parks.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _isolate_registries() -> Any:
    spec_snapshot = dict(CommandSpecRegistry._specs)  # noqa: SLF001
    handler_snapshot = dict(CommandHandlerRegistry._handlers)  # noqa: SLF001
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    yield
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    CommandSpecRegistry._specs.update(spec_snapshot)  # noqa: SLF001
    CommandHandlerRegistry._handlers.update(handler_snapshot)  # noqa: SLF001


def _register_needs_step_up(*, ran: list[str]) -> None:
    """CONSEQUENTIAL -- needs_step_up for every requester kind, `owner`
    (submit_command's own default) included, with no TraceContext fixture
    needed."""
    CommandSpecRegistry.register(CommandSpec(
        command_type=_TYPE, payload_model=_Payload, severity="consequential",
        reversible=False, undo_command_type=None,
    ))

    async def _handler(payload: _Payload, context: CommandContext) -> CommandOutcome:
        ran.append(context.command_id)
        return CommandOutcome(success=True, result={"widget_id": payload.widget_id})

    CommandHandlerRegistry.register(_TYPE, _handler)  # type: ignore[arg-type]


async def test_submit_command_parks_and_never_runs_the_handler(db: DbPool) -> None:
    ran: list[str] = []
    _register_needs_step_up(ran=ran)

    submission = await submit_command(db, _TYPE, {"widget_id": "w1"})

    assert submission.outcome is None
    assert ran == []

    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)
    assert task.status == "parked"
    assert task.gate_verdict == "needs_step_up"
    assert task.lease_owner is None


async def test_parking_opens_exactly_one_approval_item_bound_to_the_command_waiter(
    db: DbPool,
) -> None:
    _register_needs_step_up(ran=[])
    submission = await submit_command(db, _TYPE, {"widget_id": "w1"})

    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)

    rows = await db.fetch_all(
        "SELECT * FROM needs_you WHERE waiter_kind = ? AND waiter_id = ?",
        (WAITER_KIND_COMMAND, task.task_id),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "approval"
    assert row["expires_at"] is None
    assert row["resolved_cursor"] is None


async def test_parking_records_command_pending_approval_with_the_payload_summary(
    db: DbPool,
) -> None:
    _register_needs_step_up(ran=[])
    submission = await submit_command(db, _TYPE, {"widget_id": "w1"})

    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)

    rows = await db.fetch_all(
        "SELECT attrs FROM journal_events WHERE type = 'command.pending_approval' "
        "AND target_id = ?",
        (task.task_id,),
    )
    assert len(rows) == 1
    attrs = json.loads(rows[0]["attrs"])
    assert attrs["command_type"] == _TYPE
    assert attrs["requester_kind"] == "owner"
    assert attrs["outcome"] == "needs_step_up"
    assert json.loads(attrs["payload_summary"]) == {"widget_id": "w1"}
    assert task.status == "parked"


async def test_a_lease_reclaim_race_still_opens_exactly_one_item(db: DbPool) -> None:
    """Park called twice for the same command_id (the I/O matrix's own
    scenario) no-ops the SECOND open via the partial unique index -- still
    exactly one open item, and the row stays parked."""
    _register_needs_step_up(ran=[])
    submission = await submit_command(db, _TYPE, {"widget_id": "w1"})
    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)

    rows_before = await db.fetch_all(
        "SELECT id FROM needs_you WHERE waiter_kind = ? AND waiter_id = ?",
        (WAITER_KIND_COMMAND, task.task_id),
    )
    assert len(rows_before) == 1
    first_item_id = rows_before[0]["id"]

    # A second park call for the SAME command_id/task_id — simulates a
    # lease-reclaim race re-parking a row that is already parked. It REBINDS
    # onto the SAME still-open item (bind_waiter's own conditional UPDATE
    # matches it again) rather than opening a second one.
    second_item_id = await store.park_for_decision(
        task.task_id, command_type=_TYPE, command_id=submission.command_id,
        requester_kind="owner", outcome="needs_step_up",
        payload_summary='{"widget_id": "w1"}',
    )
    assert second_item_id == first_item_id

    rows_after = await db.fetch_all(
        "SELECT id FROM needs_you WHERE waiter_kind = ? AND waiter_id = ?",
        (WAITER_KIND_COMMAND, task.task_id),
    )
    assert len(rows_after) == 1
