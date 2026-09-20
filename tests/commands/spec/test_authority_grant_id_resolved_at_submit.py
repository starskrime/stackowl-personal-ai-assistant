"""Story 4.8 -- ``submit_command``'s ``authority_scope`` resolution and
``execute_command_task``'s consumption of ``tasks.authority_grant_id``: the
first LIVE dispatch path to actually consult a real ``standing_authority``
row (Story 4.6 shipped the column/parameter always hardcoded to ``None``).

Uses ``messaging.send_message`` (any declared ``severity="write",
reversible=False`` command type works -- the mechanism under test here is
generic to ``submit_command``/``execute_command_task``, not specific to
which command type happens to pass ``authority_scope``) against a real
migrated db, proving:

* an ``autonomous`` requester with a MATCHING active grant resolves it onto
  the task row and reaches ``run_at_once`` -- no approval item, the handler
  actually runs;
* an ``autonomous`` requester with NO matching grant gets no resolved id and
  parks (``needs_step_up``);
* an ``autonomous`` requester with a REVOKED grant (found inactive) also
  parks -- ``find_active`` correctly excludes it;
* the 3 owl/owner-attended types never even attempt resolution (Boundaries:
  the grant carve-out only ever applies to ``requester_kind="autonomous"``)
  -- passing ``authority_scope`` for an ``owner`` run resolves nothing and
  still parks, proving the ``requester_kind == "autonomous"`` guard in
  ``submit_command`` itself, not just in ``decide()``.
"""

from __future__ import annotations

import pytest

import stackowl.notifications.commands  # noqa: F401 -- registration side effect
from stackowl.authz.standing_authority import grant, revoke
from stackowl.commands.spec.submit import submit_command
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.notifications.commands import SEND_MESSAGE
from stackowl.notifications.router import Notification
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.consent import PRINCIPAL_AUTONOMOUS_SCHEDULER

pytestmark = pytest.mark.asyncio


class _FakeDeliverer:
    def __init__(self) -> None:
        self.calls: list[Notification] = []

    async def deliver(self, notification: Notification, *, context: object = None) -> str:
        self.calls.append(notification)
        return "delivered"


def _payload() -> dict[str, object]:
    return {"message": "hi", "channel": "cli", "category": "agent_message"}


async def _submit_autonomous(
    db: DbPool, deliverer: _FakeDeliverer, *, scope_id: str, command_id: str,
) -> object:
    ttoken = TraceContext.start(
        session_key=None, trace_id="t-authority", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )
    stoken = set_services(StepServices(db_pool=db, proactive_deliverer=deliverer))
    try:
        return await submit_command(
            db, SEND_MESSAGE, _payload(), command_id=command_id,
            authority_scope=("job", scope_id),
        )
    finally:
        TraceContext.reset(ttoken)
        reset_services(stoken)


async def test_autonomous_with_matching_grant_resolves_and_runs_at_once(
    tmp_db: DbPool,
) -> None:
    await grant(
        tmp_db, scope_kind="job", scope_id="job-match-1",
        command_type=SEND_MESSAGE, granted_by="autonomous", provenance="seeded",
    )
    deliverer = _FakeDeliverer()

    submission = await _submit_autonomous(
        tmp_db, deliverer, scope_id="job-match-1", command_id="cmd-auth-1",
    )

    assert submission.outcome is not None  # ran at once, no park
    assert submission.outcome.success is True
    assert len(deliverer.calls) == 1

    rows = await tmp_db.fetch_all(
        "SELECT authority_grant_id, status FROM tasks WHERE command_id = ?", ("cmd-auth-1",),
    )
    assert len(rows) == 1
    assert rows[0]["authority_grant_id"] is not None
    assert rows[0]["status"] == "completed"


async def test_autonomous_with_no_matching_grant_parks(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()

    submission = await _submit_autonomous(
        tmp_db, deliverer, scope_id="job-no-grant", command_id="cmd-auth-2",
    )

    assert submission.outcome is None  # parked awaiting a decision
    assert deliverer.calls == []
    rows = await tmp_db.fetch_all(
        "SELECT authority_grant_id, status FROM tasks WHERE command_id = ?", ("cmd-auth-2",),
    )
    assert len(rows) == 1
    assert rows[0]["authority_grant_id"] is None
    assert rows[0]["status"] == "parked"


async def test_autonomous_with_a_revoked_grant_parks(tmp_db: DbPool) -> None:
    await grant(
        tmp_db, scope_kind="job", scope_id="job-revoked-1",
        command_type=SEND_MESSAGE, granted_by="autonomous", provenance="seeded",
    )
    await revoke(
        tmp_db, scope_kind="job", scope_id="job-revoked-1",
        command_type=SEND_MESSAGE, revoked_by="owner",
    )
    deliverer = _FakeDeliverer()

    submission = await _submit_autonomous(
        tmp_db, deliverer, scope_id="job-revoked-1", command_id="cmd-auth-3",
    )

    assert submission.outcome is None
    assert deliverer.calls == []


async def test_owner_requester_never_resolves_authority_even_with_a_matching_grant(
    tmp_db: DbPool,
) -> None:
    """AD-1/decide()'s carve-out only ever applies to `requester_kind=
    "autonomous"` -- an attending owner run must park regardless of a
    matching grant existing for the SAME scope/command_type."""
    await grant(
        tmp_db, scope_kind="job", scope_id="job-owner-1",
        command_type=SEND_MESSAGE, granted_by="autonomous", provenance="seeded",
    )
    deliverer = _FakeDeliverer()
    stoken = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        submission = await submit_command(
            tmp_db, SEND_MESSAGE, _payload(), command_id="cmd-auth-4",
            authority_scope=("job", "job-owner-1"),
        )
    finally:
        reset_services(stoken)

    assert submission.outcome is None  # still parks — owner is attending
    assert deliverer.calls == []
    rows = await tmp_db.fetch_all(
        "SELECT authority_grant_id FROM tasks WHERE command_id = ?", ("cmd-auth-4",),
    )
    assert rows[0]["authority_grant_id"] is None  # never even resolved
