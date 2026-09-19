"""``submit_command`` — the I/O matrix's registered/unregistered/retry rows (Story 4.3).

A real migrated SQLite ``DbPool`` (``tests/_schema_template.py``) rather than a
double: ``submit_command`` writes a ``tasks`` row through
``DurableTaskStore.create`` and the idempotent-retry row depends on the REAL
``idx_tasks_command_id`` partial unique index (migration 0151) actually firing.
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
from stackowl.commands.spec.errors import CommandTypeNotDeclaredError
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.commands.spec.submit import submit_command
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_TYPE = "widget.frobnicate"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    widget_id: str = Field(min_length=1)


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "submit_command.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _isolate_registries() -> Any:
    """Snapshot-and-restore, not reset-to-empty — these are PROCESS-WIDE
    singletons (mirrors ``CommandRegistry``'s own ``_isolate_registry``
    fixture in ``tests/commands/test_dry_run.py``). A bare ``reset()`` with
    no restore would permanently wipe ``scheduler/commands.py``'s pilot
    ``CommandSpec``s/handlers for the REST of the pytest session the moment
    this module happens to import (and therefore register) before them —
    an order-dependent failure any other test importing this module first
    would otherwise hit."""
    spec_snapshot = dict(CommandSpecRegistry._specs)  # noqa: SLF001 — test-only introspection
    handler_snapshot = dict(CommandHandlerRegistry._handlers)  # noqa: SLF001
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    yield
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    CommandSpecRegistry._specs.update(spec_snapshot)  # noqa: SLF001
    CommandHandlerRegistry._handlers.update(handler_snapshot)  # noqa: SLF001


def _register_pilot(*, ran: list[str] | None = None, succeed: bool = True) -> None:
    CommandSpecRegistry.register(CommandSpec(
        command_type=_TYPE, payload_model=_Payload, severity="write",
        reversible=False, undo_command_type=None,
    ))

    async def _handler(payload: _Payload, context: CommandContext) -> CommandOutcome:
        if ran is not None:
            ran.append(context.command_id)
        if not succeed:
            return CommandOutcome(success=False, error="handler refused")
        return CommandOutcome(success=True, result={"widget_id": payload.widget_id})

    CommandHandlerRegistry.register(_TYPE, _handler)  # type: ignore[arg-type]


async def test_submit_command_with_a_registered_type_enqueues_and_runs(
    db: DbPool,
) -> None:
    ran: list[str] = []
    _register_pilot(ran=ran)
    submission = await submit_command(db, _TYPE, {"widget_id": "w1"})
    assert submission.command_id
    assert submission.task_id
    assert submission.outcome is not None
    assert submission.outcome.success is True
    assert ran == [submission.command_id]

    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)
    assert task.kind == "command"
    assert task.command_type == _TYPE
    assert task.status == "completed"

    enqueued = await db.fetch_all(
        "SELECT attrs FROM journal_events WHERE type = 'command.enqueued' "
        "AND target_id = ?",
        (task.task_id,),
    )
    assert len(enqueued) == 1
    enqueued_attrs = json.loads(enqueued[0]["attrs"])
    assert enqueued_attrs["command_type"] == _TYPE
    assert enqueued_attrs["requester_kind"] == "owner"

    completed = await db.fetch_all(
        "SELECT attrs FROM journal_events WHERE type = 'command.completed' "
        "AND target_id = ?",
        (task.task_id,),
    )
    assert len(completed) == 1
    assert json.loads(completed[0]["attrs"])["command_type"] == _TYPE


async def test_a_failing_handler_produces_exactly_one_command_failed_row(
    db: DbPool,
) -> None:
    _register_pilot(succeed=False)
    submission = await submit_command(db, _TYPE, {"widget_id": "w1"})

    assert submission.outcome is not None
    assert submission.outcome.success is False

    failed = await db.fetch_all(
        "SELECT attrs FROM journal_events WHERE type = 'command.failed' "
        "AND target_id = ?",
        (submission.task_id,),
    )
    assert len(failed) == 1
    attrs = json.loads(failed[0]["attrs"])
    assert attrs["command_type"] == _TYPE
    assert attrs["failure_class"] == "command_handler_failed"

    # No command.completed row for a failed run.
    completed = await db.fetch_all(
        "SELECT 1 FROM journal_events WHERE type = 'command.completed' "
        "AND target_id = ?",
        (submission.task_id,),
    )
    assert completed == []


async def test_submit_command_with_an_unregistered_type_raises() -> None:
    with pytest.raises(CommandTypeNotDeclaredError) as exc_info:
        await submit_command(None, "no.such.type", {})  # type: ignore[arg-type]
    assert "no.such.type" in str(exc_info.value)


async def test_submit_command_called_twice_with_the_same_command_id_is_idempotent(
    db: DbPool,
) -> None:
    ran: list[str] = []
    _register_pilot(ran=ran)
    first = await submit_command(db, _TYPE, {"widget_id": "w1"}, command_id="cid-retry-1")
    assert first.command_id == "cid-retry-1"
    assert first.outcome is not None and first.outcome.success

    second = await submit_command(db, _TYPE, {"widget_id": "w1"}, command_id="cid-retry-1")
    assert second.command_id == "cid-retry-1"
    assert second.task_id == first.task_id
    # The retry reports the REAL prior outcome — not "could not confirm it
    # ran" for a command that plainly already succeeded.
    assert second.outcome is not None
    assert second.outcome.success is True

    # The handler ran exactly once — the retry never re-enqueued or re-ran it.
    assert ran == ["cid-retry-1"]
    store = DurableTaskStore(db)
    rows = await store.list()
    command_rows = [t for t in rows if t.command_id == "cid-retry-1"]
    assert len(command_rows) == 1


async def test_submit_command_payload_is_validated_against_the_declared_model(
    db: DbPool,
) -> None:
    _register_pilot()
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        await submit_command(db, _TYPE, {"not_a_widget_id": 1})


async def test_run_inline_false_enqueues_without_claiming_and_notifies(
    db: DbPool,
) -> None:
    """The gateway-role half (AD-1): no local loop to claim against — the row
    is left pending for the tick loop, and the caller's notify callback runs."""
    ran: list[str] = []
    _register_pilot(ran=ran)
    notified = []

    async def _notify() -> None:
        notified.append(True)

    submission = await submit_command(
        db, _TYPE, {"widget_id": "w1"}, run_inline=False, notify_enqueued=_notify,
    )

    assert submission.outcome is None  # not observed by THIS call
    assert notified == [True]
    assert ran == []  # never claimed or run inline

    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)
    assert task.status == "pending"
    assert task.lease_owner is None


async def test_run_inline_false_tolerates_a_raising_notify_callback(
    db: DbPool,
) -> None:
    """Best-effort: a failed notify costs latency, never the row."""
    _register_pilot()

    async def _boom() -> None:
        raise RuntimeError("no connection")

    submission = await submit_command(
        db, _TYPE, {"widget_id": "w1"}, run_inline=False, notify_enqueued=_boom,
    )

    assert submission.outcome is None
    store = DurableTaskStore(db)
    task = await store.get_by_command_id(submission.command_id)
    assert task.status == "pending"
