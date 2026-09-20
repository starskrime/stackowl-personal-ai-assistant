"""Story 4.5 -- ``request_undo``: undo submits the declared undo command
through the one door (FR31), and is refused once superseded or 24 hours pass
(FR88), with a ``{code, reason, remedy}`` refusal recorded to the journal.

A real migrated SQLite ``DbPool`` (mirrors ``test_submit_command.py``'s own
rationale): ``request_undo`` reads real ``tasks``/``journal_events`` rows a
double could not stand in for.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.commands.spec.submit import submit_command
from stackowl.commands.spec.undo import request_undo
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_ON = "widget.turn_on"
_OFF = "widget.turn_off"
_ONE_WAY = "widget.detonate"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    widget_id: str = Field(min_length=1)


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "request_undo.db"
    seed_schema(db_path)
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def _isolate_registries() -> Any:
    """Snapshot-and-restore — see ``test_submit_command.py``'s identical
    fixture for why (process-wide singletons)."""
    spec_snapshot = dict(CommandSpecRegistry._specs)  # noqa: SLF001
    handler_snapshot = dict(CommandHandlerRegistry._handlers)  # noqa: SLF001
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    yield
    CommandSpecRegistry.reset()
    CommandHandlerRegistry.reset()
    CommandSpecRegistry._specs.update(spec_snapshot)  # noqa: SLF001
    CommandHandlerRegistry._handlers.update(handler_snapshot)  # noqa: SLF001


def _register_reversible_pair() -> None:
    CommandSpecRegistry.register(CommandSpec(
        command_type=_ON, payload_model=_Payload, severity="write",
        reversible=True, undo_command_type=_OFF,
    ))
    CommandSpecRegistry.register(CommandSpec(
        command_type=_OFF, payload_model=_Payload, severity="write",
        reversible=True, undo_command_type=_ON,
    ))

    async def _handler(payload: _Payload, context: CommandContext) -> CommandOutcome:
        return CommandOutcome(success=True, result={"widget_id": payload.widget_id})

    CommandHandlerRegistry.register(_ON, _handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(_OFF, _handler)  # type: ignore[arg-type]


def _register_irreversible() -> None:
    CommandSpecRegistry.register(CommandSpec(
        command_type=_ONE_WAY, payload_model=_Payload, severity="write",
        reversible=False,
    ))

    async def _handler(payload: _Payload, context: CommandContext) -> CommandOutcome:
        return CommandOutcome(success=True, result={"widget_id": payload.widget_id})

    CommandHandlerRegistry.register(_ONE_WAY, _handler)  # type: ignore[arg-type]


async def _backdate(db: DbPool, task_id: str, *, days: int) -> None:
    # A Python-computed, timezone-AWARE isoformat string -- matching exactly
    # what `mark_delivered` itself writes (`datetime.now(UTC).isoformat()`).
    # SQLite's own `datetime('now', ...)` produces a NAIVE string with no
    # offset, which `_row_to_task`'s `datetime.fromisoformat()` would then
    # read back as offset-naive -- a real defect only this test-only helper
    # would introduce, never production code (every real `delivered_at` is
    # always written by `mark_delivered`).
    then = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    await db.execute(
        "UPDATE tasks SET delivered_at = ?, updated_at = ? WHERE task_id = ?",
        (then, then, task_id),
    )


async def test_undo_submits_the_declared_undo_type_through_the_one_door(
    db: DbPool,
) -> None:
    _register_reversible_pair()
    original = await submit_command(db, _ON, {"widget_id": "w1"})
    assert original.outcome is not None and original.outcome.success

    outcome = await request_undo(db, original.command_id)

    assert outcome.refusal is None
    assert outcome.submission is not None
    assert outcome.submission.outcome is not None
    assert outcome.submission.outcome.success is True

    store = DurableTaskStore(db)
    undo_task = await store.get_by_command_id(outcome.submission.command_id)
    assert undo_task.command_type == _OFF
    assert json.loads(undo_task.command_payload or "{}") == {"widget_id": "w1"}


async def test_undo_refused_when_the_24_hour_window_has_passed(db: DbPool) -> None:
    _register_reversible_pair()
    original = await submit_command(db, _ON, {"widget_id": "w1"})
    assert original.outcome is not None
    store = DurableTaskStore(db)
    task = await store.get_by_command_id(original.command_id)
    await _backdate(db, task.task_id, days=2)

    outcome = await request_undo(db, original.command_id)

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "expired"
    assert outcome.refusal.reason
    assert outcome.refusal.remedy

    refused = await db.fetch_all(
        "SELECT attrs FROM journal_events WHERE type = 'command.undo_refused' "
        "AND target_id = ?",
        (task.task_id,),
    )
    assert len(refused) == 1
    attrs = json.loads(refused[0]["attrs"])
    assert attrs["command_type"] == _ON
    assert attrs["code"] == "expired"


async def test_undo_refused_when_a_later_command_touched_the_same_target(
    db: DbPool,
) -> None:
    _register_reversible_pair()
    original = await submit_command(db, _ON, {"widget_id": "w1"})
    assert original.outcome is not None
    # A LATER command (not an undo — an ordinary new submission) touches the
    # same target (same payload).
    later = await submit_command(db, _OFF, {"widget_id": "w1"})
    assert later.outcome is not None and later.outcome.success

    outcome = await request_undo(db, original.command_id)

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "superseded"


async def test_undo_refused_for_an_unknown_command_id(db: DbPool) -> None:
    _register_reversible_pair()

    outcome = await request_undo(db, "no-such-command-id")

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "not_found"


async def test_undo_refused_for_a_command_that_has_not_completed_yet(db: DbPool) -> None:
    _register_reversible_pair()
    store = DurableTaskStore(db)
    await store.create(DurableTask(
        task_id="cmd-pending-1",
        goal=f"command:{_ON}",
        status="pending",
        kind="command",
        command_type=_ON,
        command_payload=_Payload(widget_id="w1").model_dump_json(),
        command_id="pending-1",
        requester_kind="owner",
        trigger_kind="command",
    ))

    outcome = await request_undo(db, "pending-1")

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "not_completed"


async def test_undo_refused_for_an_irreversible_command(db: DbPool) -> None:
    # NOT submitted through `submit_command` — an irreversible command
    # ALWAYS needs step-up (`authz.action_policy.decide`: "severity ==
    # CONSEQUENTIAL or not reversible" -> "needs_step_up"), so it would park
    # rather than complete inline. This test is about `request_undo`'s OWN
    # reversibility check, not the gate, so the row is inserted directly
    # already `completed` — exactly what a real irreversible command's row
    # looks like once its (separately-approved) step-up resumes it.
    _register_irreversible()
    store = DurableTaskStore(db)
    await store.create(DurableTask(
        task_id="cmd-one-way-1",
        goal=f"command:{_ONE_WAY}",
        status="pending",
        kind="command",
        command_type=_ONE_WAY,
        command_payload=_Payload(widget_id="w1").model_dump_json(),
        command_id="one-way-1",
        requester_kind="owner",
        trigger_kind="command",
    ))
    await store.mark_delivered("cmd-one-way-1", result="done")

    outcome = await request_undo(db, "one-way-1")

    assert outcome.submission is None
    assert outcome.refusal is not None
    assert outcome.refusal.code == "not_reversible"
