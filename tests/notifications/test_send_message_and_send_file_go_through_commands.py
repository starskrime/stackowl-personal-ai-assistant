"""``messaging.send_message``/``messaging.send_file`` -- Story 4.8's
handler-level I/O matrix: the receipt-check-first/write-last idempotency
guard, the honest delivered/failed mapping, and the no-db/no-deliverer
degradations.

Modelled on ``tests/objectives/test_set_objective_is_a_command.py``'s own
end-to-end style: a :class:`DurableTask` with ``gate_verdict="approved"``
skips the action-policy gate entirely (both command types are declared
IRREVERSIBLE -- an UNGATED call would always park for an attending owner)
and reaches the real handler through :func:`execute_command_task` -- the
correct unit boundary for testing a handler's own mutation.

The TOOL-level parking behavior (an owner-attended send always needs
step-up) is proven in ``tests/tools/scheduling/test_send_message.py``/
``test_send_file.py``; this file proves what happens once a command
actually reaches its handler.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import pytest

import stackowl.notifications.commands  # noqa: F401 -- registration side effect
from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.spec.execute import execute_command_task
from stackowl.db.pool import DbPool
from stackowl.notifications.commands import SEND_FILE, SEND_MESSAGE
from stackowl.notifications.router import Notification
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _channels() -> AsyncIterator[None]:
    reg = ChannelRegistry.instance()
    reg.reset()
    reg.register(_FakeAdapter("telegram"))
    reg.register(_FakeAdapter("cli"))
    yield
    reg.reset()


class _FakeDeliverer:
    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[Notification] = []
        self.contexts: list[object] = []

    async def deliver(self, notification: Notification, *, context: object = None) -> str:
        self.calls.append(notification)
        self.contexts.append(context)
        return self.status


def _send_message_task(
    command_id: str, *, message: str = "hi", channel: str = "cli",
) -> DurableTask:
    payload = {"message": message, "channel": channel, "category": "agent_message"}
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{SEND_MESSAGE}", status="running",
        kind="command", command_type=SEND_MESSAGE,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="owner", gate_verdict="approved",
    )


def _send_file_task(
    command_id: str, *, file_path: str, caption: str = "", channel: str = "cli",
) -> DurableTask:
    payload = {
        "file_path": file_path, "caption": caption, "channel": channel,
        "category": "agent_file",
    }
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{SEND_FILE}", status="running",
        kind="command", command_type=SEND_FILE,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="owner", gate_verdict="approved",
    )


# --------------------------------------------------------------------------- send_message


async def test_send_message_delivers_and_records_receipt(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_send_message_task("cmd-sm-1"))
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["delivery_status"] == "delivered"
    assert len(deliverer.calls) == 1
    assert deliverer.calls[0].channel_name == "cli"
    assert deliverer.calls[0].message == "hi"

    receipts = await tmp_db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", ("cmd-sm-1",),
    )
    assert len(receipts) == 1


async def test_send_message_transport_failed_is_unsuccessful(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="failed")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_send_message_task("cmd-sm-2"))
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.result["delivery_status"] == "failed"
    # The receipt is still written LAST, once the terminal (failed) status is
    # known — a lease-reclaim re-run must not re-send.
    receipts = await tmp_db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", ("cmd-sm-2",),
    )
    assert len(receipts) == 1


async def test_send_message_re_run_with_the_same_command_id_does_not_resend(
    tmp_db: DbPool,
) -> None:
    """I/O matrix: 'Re-run send_file after a simulated lease reclaim (same
    command_id twice) -- second call's receipt-read-first finds the existing
    row, treated as already-sent, no duplicate transport'. Same guard for
    send_message."""
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        first = await execute_command_task(_send_message_task("cmd-sm-reclaim"))
        second = await execute_command_task(_send_message_task("cmd-sm-reclaim"))
    finally:
        reset_services(token)

    assert first.success is True
    # Review fix — a replay must NOT re-assert delivery as fact: the receipt
    # only proves an attempt happened, never its outcome (command_receipts
    # carries no status column). success=False here is what lets the command
    # escalate through the normal retry/dead-letter ladder instead of a
    # silent false "done".
    assert second.success is False
    assert second.result.get("already_sent") is True
    assert second.result.get("delivery_status") == "unknown"
    assert len(deliverer.calls) == 1, "the second call must not transport again"


async def test_send_message_replay_after_a_failed_first_attempt_does_not_claim_success(
    tmp_db: DbPool,
) -> None:
    """Review regression: the receipt is written unconditionally, whether the
    first attempt delivered or genuinely failed (Design Notes). A replay of a
    command_id whose first attempt FAILED must not claim delivered/success —
    the receipt alone proves nothing about the original outcome."""
    deliverer = _FakeDeliverer(status="failed")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        first = await execute_command_task(_send_message_task("cmd-sm-failed-replay"))
        second = await execute_command_task(_send_message_task("cmd-sm-failed-replay"))
    finally:
        reset_services(token)

    assert first.success is False
    assert first.result.get("delivery_status") == "failed"
    # The replay must not upgrade a failed attempt into an asserted success.
    assert second.success is False
    assert second.result.get("delivery_status") != "delivered"
    assert second.result.get("already_sent") is True
    assert len(deliverer.calls) == 1, "the second call must not transport again"


async def test_send_message_no_db_pool_fails_cleanly() -> None:
    token = set_services(StepServices())
    try:
        outcome = await execute_command_task(_send_message_task("cmd-sm-no-db"))
    finally:
        reset_services(token)
    assert outcome.success is False
    assert "database" in (outcome.error or "")


async def test_send_message_no_deliverer_fails_cleanly(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        outcome = await execute_command_task(_send_message_task("cmd-sm-no-deliverer"))
    finally:
        reset_services(token)
    assert outcome.success is False
    assert "deliverer" in (outcome.error or "")


async def test_send_message_context_is_threaded_to_deliver(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        await execute_command_task(_send_message_task("cmd-sm-ctx"))
    finally:
        reset_services(token)
    assert deliverer.contexts[0] is not None
    assert deliverer.contexts[0].command_id == "cmd-sm-ctx"  # type: ignore[union-attr]


# --------------------------------------------------------------------------- send_file


async def test_send_file_delivers_and_records_receipt(tmp_path: Path, tmp_db: DbPool) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(
            _send_file_task("cmd-sf-1", file_path=str(f), caption="here")
        )
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["delivery_status"] == "delivered"
    assert len(deliverer.calls) == 1
    assert deliverer.calls[0].file_path == str(f)
    assert deliverer.calls[0].message == "here"


async def test_send_file_re_run_with_the_same_command_id_does_not_resend(
    tmp_path: Path, tmp_db: DbPool,
) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        first = await execute_command_task(
            _send_file_task("cmd-sf-reclaim", file_path=str(f))
        )
        second = await execute_command_task(
            _send_file_task("cmd-sf-reclaim", file_path=str(f))
        )
    finally:
        reset_services(token)

    assert first.success is True
    assert second.success is False
    assert second.result.get("already_sent") is True
    assert second.result.get("delivery_status") == "unknown"
    assert len(deliverer.calls) == 1


async def test_send_file_transport_failed_is_unsuccessful(
    tmp_path: Path, tmp_db: DbPool,
) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    deliverer = _FakeDeliverer(status="failed")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(
            _send_file_task("cmd-sf-2", file_path=str(f))
        )
    finally:
        reset_services(token)
    assert outcome.success is False
    assert outcome.result["delivery_status"] == "failed"


async def test_send_file_no_db_pool_fails_cleanly(tmp_path: Path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    token = set_services(StepServices())
    try:
        outcome = await execute_command_task(_send_file_task("cmd-sf-no-db", file_path=str(f)))
    finally:
        reset_services(token)
    assert outcome.success is False
    assert "database" in (outcome.error or "")
