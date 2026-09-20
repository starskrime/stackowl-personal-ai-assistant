"""Story 4.6 -- ``authz.commands`` registers ``authority.grant``/
``authority.revoke``, both always decide ``needs_step_up`` (FR37), and the
resumed-after-step-up path actually reaches ``authz.standing_authority``.

Modelled on ``tests/commands/spec/test_undo_types_resolve_in_the_registry.py``'s
own "import for the registration side effect, then assert against the live
registry" style; the end-to-end test mirrors ``tests/commands/spec/
test_execute_command_task_gate.py``'s ``DurableTask`` construction. The
registry/decision assertions are individually ``pytest.mark.tripwire`` (cheap,
pure, no I/O); the end-to-end handler tests below are not -- they hit a real
migrated sqlite db via ``tmp_db``, the same weight class ``tests/scheduler/
test_pause_resume_are_commands.py`` already runs outside the tripwire sweep.
"""

from __future__ import annotations

import json

import pytest

import stackowl.authz.commands  # noqa: F401 -- import registers the two CommandSpecs
from stackowl.authz.action_policy import decide
from stackowl.authz.commands import AUTHORITY_GRANT, AUTHORITY_REVOKE
from stackowl.authz.standing_authority import find_active
from stackowl.commands.spec.execute import execute_command_task
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services


@pytest.mark.tripwire
def test_authority_grant_is_registered_consequential_and_reversible() -> None:
    spec = CommandSpecRegistry.get(AUTHORITY_GRANT)
    assert spec.severity == "consequential"
    assert spec.reversible is True
    assert spec.undo_command_type == AUTHORITY_REVOKE


@pytest.mark.tripwire
def test_authority_revoke_is_registered_consequential_and_reversible() -> None:
    spec = CommandSpecRegistry.get(AUTHORITY_REVOKE)
    assert spec.severity == "consequential"
    assert spec.reversible is True
    assert spec.undo_command_type == AUTHORITY_GRANT


@pytest.mark.tripwire
def test_both_share_the_same_payload_shape() -> None:
    """4.5's undo path re-submits the ORIGINAL command's own payload against
    the declared undo type -- grant/revoke must accept the identical shape."""
    grant_spec = CommandSpecRegistry.get(AUTHORITY_GRANT)
    revoke_spec = CommandSpecRegistry.get(AUTHORITY_REVOKE)
    assert grant_spec.payload_model is revoke_spec.payload_model


@pytest.mark.tripwire
def test_both_handlers_are_registered() -> None:
    assert callable(CommandHandlerRegistry.get(AUTHORITY_GRANT))
    assert callable(CommandHandlerRegistry.get(AUTHORITY_REVOKE))


@pytest.mark.tripwire
@pytest.mark.parametrize("requester_kind", ["owner", "owl", "voice-unverified", "autonomous"])
@pytest.mark.parametrize("command_type", [AUTHORITY_GRANT, AUTHORITY_REVOKE])
def test_every_requester_kind_needs_step_up_even_with_a_matching_grant(
    command_type: str, requester_kind: str,
) -> None:
    """FR37 -- no self-grant, no voice: the declared severity alone already
    forces this, with zero new branch in `decide()`. A supplied
    `authority_grant_id` (an attempt to bypass via a stale/forged grant id)
    changes nothing -- severity is checked before authority_grant_id ever is."""
    spec = CommandSpecRegistry.get(command_type)
    result = decide(
        severity=spec.severity, reversible=spec.reversible,
        requester_kind=requester_kind,  # type: ignore[arg-type]
        authority_grant_id="forged-grant-id",
    )
    assert result.outcome == "needs_step_up"
    assert result.authority_grant_id is None


def _grant_task(*, gate_verdict: str | None = "approved") -> DurableTask:
    return DurableTask(
        task_id="cmd-authority-1", goal=f"command:{AUTHORITY_GRANT}", status="running",
        kind="command", command_type=AUTHORITY_GRANT,
        command_payload=json.dumps({
            "scope_kind": "job", "scope_id": "job-1",
            "command_type": "delivery.send_message",
        }),
        command_id="cmd-authority-1", requester_kind="owner", gate_verdict=gate_verdict,
    )


def _revoke_task(*, gate_verdict: str | None = "approved") -> DurableTask:
    return DurableTask(
        task_id="cmd-authority-2", goal=f"command:{AUTHORITY_REVOKE}", status="running",
        kind="command", command_type=AUTHORITY_REVOKE,
        command_payload=json.dumps({
            "scope_kind": "job", "scope_id": "job-1",
            "command_type": "delivery.send_message",
        }),
        command_id="cmd-authority-2", requester_kind="owner", gate_verdict=gate_verdict,
    )


@pytest.mark.asyncio
async def test_the_resumed_grant_handler_actually_writes_standing_authority(
    tmp_db: DbPool,
) -> None:
    """A COMMAND row resumed after its step-up Needs-you item was answered
    (`gate_verdict="approved"`, Story 4.4's own mechanism) skips straight to
    the handler -- which must actually call `standing_authority.grant`, not
    just decide correctly."""
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await execute_command_task(_grant_task())
    finally:
        reset_services(token)

    assert outcome.success is True
    active = await find_active(
        tmp_db, scope_kind="job", scope_id="job-1", command_type="delivery.send_message",
    )
    assert active is not None
    assert active.granted_by == "owner"


@pytest.mark.asyncio
async def test_the_resumed_revoke_handler_actually_closes_standing_authority(
    tmp_db: DbPool,
) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        await execute_command_task(_grant_task(gate_verdict="approved"))
        outcome = await execute_command_task(_revoke_task())
    finally:
        reset_services(token)

    assert outcome.success is True
    active = await find_active(
        tmp_db, scope_kind="job", scope_id="job-1", command_type="delivery.send_message",
    )
    assert active is None


@pytest.mark.asyncio
async def test_grant_handler_fails_cleanly_with_no_db_pool_configured() -> None:
    token = set_services(StepServices())  # db_pool defaults to None
    try:
        outcome = await execute_command_task(_grant_task())
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.error == "standing authority unavailable (no database configured)"


@pytest.mark.asyncio
async def test_revoke_handler_fails_cleanly_with_no_db_pool_configured() -> None:
    token = set_services(StepServices())  # db_pool defaults to None
    try:
        outcome = await execute_command_task(_revoke_task())
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.error == "standing authority unavailable (no database configured)"
