"""``notifications.broadcast_urgent``/``notifications.deliver_brief`` --
Story 4.8's handler-level I/O matrix, plus proof that ``UrgentCommand``/
``MorningBriefHandler`` submit through the one door instead of calling a
delivery mutator directly.

Handler-level tests mirror ``tests/objectives/test_set_objective_is_a_
command.py``'s "gate_verdict='approved' + execute_command_task" unit
boundary. Wiring tests drive the REAL tool/handler entry points with
``get_services()``/``TraceContext`` set up, proving:

* an owner-attended ``/urgent`` always parks for step-up (no owner carve-out
  for an irreversible broadcast);
* an AUTONOMOUS (scheduler-driven) morning-brief run with a MATCHING standing-
  authority grant delivers with no new approval (the grandfathered/seeded
  carve-out — AC3), while one with none parks.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import stackowl.notifications.commands  # noqa: F401 -- registration side effect
from stackowl.authz.standing_authority import grant
from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.spec.execute import execute_command_task
from stackowl.commands.urgent_command import UrgentCommand
from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.infra.trace import TraceContext
from stackowl.notifications.commands import BROADCAST_URGENT, DELIVER_BRIEF
from stackowl.notifications.router import Notification
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.consent import PRINCIPAL_AUTONOMOUS_SCHEDULER
from tests._story_7_2_helpers import disable_guard, make_job

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
def _channels() -> Any:
    reg = ChannelRegistry.instance()
    reg.reset()
    reg.register(_FakeAdapter("telegram"))
    reg.register(_FakeAdapter("cli"))
    yield
    reg.reset()


class _FakeDeliverer:
    #: ProactiveJobDeliverer reads this attribute (the durable-NACK outbox);
    #: None is the same "unwired" shape ProactiveDeliverer itself defaults to.
    outbox = None

    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[Notification] = []

    async def deliver(
        self, notification: Notification, *,
        surface_undelivered: bool = True, context: object = None,
    ) -> str:
        self.calls.append(notification)
        return self.status


class _StubBridge:
    async def recall(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []

    async def list_staged(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []


def _brief_settings() -> Settings:
    return Settings(brief=BriefSettings(channels=["telegram"]), system=SystemSettings(timezone="UTC"))


# --------------------------------------------------------------------------- broadcast_urgent handler


def _urgent_task(command_id: str, channels: list[str], message: str = "alert") -> DurableTask:
    payload = {"message": message, "channels": channels}
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{BROADCAST_URGENT}", status="running",
        kind="command", command_type=BROADCAST_URGENT,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="owner", gate_verdict="approved",
    )


async def test_broadcast_urgent_counts_only_real_deliveries(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_urgent_task("cmd-u-1", ["telegram", "cli"]))
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["delivered"] == 2
    assert outcome.result["failed"] == 0
    assert len(deliverer.calls) == 2


async def test_broadcast_urgent_zero_channels_is_not_trivially_successful(
    tmp_db: DbPool,
) -> None:
    """Review fix — nothing configured to receive the broadcast must NOT read
    as a trivial success; it is a real failure with an explicit reason."""
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_urgent_task("cmd-u-zero", []))
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.error == "no channel configured to receive the broadcast"
    assert outcome.result["total"] == 0
    assert deliverer.calls == []


async def test_broadcast_urgent_partial_failure_is_reported(tmp_db: DbPool) -> None:
    class _MixedDeliverer:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def deliver(self, notification: Notification, *, context: object = None) -> str:
            self.calls.append(notification.channel_name or "")
            return "delivered" if notification.channel_name == "telegram" else "failed"

    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=_MixedDeliverer()))
    try:
        outcome = await execute_command_task(_urgent_task("cmd-u-2", ["telegram", "cli"]))
    finally:
        reset_services(token)

    assert outcome.result["delivered"] == 1
    assert outcome.result["failed"] == 1
    assert outcome.success is True  # at least one channel got it


async def test_broadcast_urgent_re_run_with_the_same_command_id_does_not_resend(
    tmp_db: DbPool,
) -> None:
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        await execute_command_task(_urgent_task("cmd-u-reclaim", ["telegram"]))
        await execute_command_task(_urgent_task("cmd-u-reclaim", ["telegram"]))
    finally:
        reset_services(token)
    assert len(deliverer.calls) == 1


# --------------------------------------------------------------------------- /urgent wiring


async def test_urgent_command_owner_attended_always_parks(tmp_db: DbPool) -> None:
    """AC2/Design Notes — an owner-attended irreversible broadcast always
    needs step-up; the fake deliverer is never invoked synchronously."""
    deliverer = _FakeDeliverer()
    cmd = UrgentCommand(deliverer=deliverer, channels=["telegram", "cli"])
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    ttoken = TraceContext.start(session_key="s", trace_id="t", interactive=True, channel="cli")
    try:
        result = await cmd.handle("system alert", _fake_state())
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert "awaiting your approval" in result
    assert deliverer.calls == []
    rows = await tmp_db.fetch_all(
        "SELECT status FROM tasks WHERE command_type = 'notifications.broadcast_urgent'",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "parked"


def _fake_state() -> Any:
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="trace-test", session_key="s", input_text="",
        channel="cli", owl_name="secretary", pipeline_step="",
    )


# --------------------------------------------------------------------------- morning brief wiring


async def test_morning_brief_autonomous_with_matching_grant_delivers_without_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    """AC3 — a scheduled morning-brief run under a MATCHING standing-
    authority grant delivers with no new approval."""
    disable_guard(monkeypatch)
    job = make_job(
        handler="morning_brief", target_channels=["telegram"], target_addresses={"telegram": 12345},
    )
    await grant(
        tmp_db, scope_kind="job", scope_id=job.job_id,
        command_type=DELIVER_BRIEF, granted_by="autonomous", provenance="seeded",
    )
    deliverer = _FakeDeliverer(status="delivered")
    handler = MorningBriefHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        scheduler=JobScheduler(db=tmp_db), db=tmp_db, event_bus=EventBus(),
        settings=_brief_settings(),
        proactive_deliverer=deliverer,
        delivery_ledger=_ledger(tmp_db),
    )
    ttoken = TraceContext.start(
        session_key=None, trace_id="t-brief", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is True
    assert result.metadata["delivery_status"] == "delivered"
    assert len(deliverer.calls) >= 1


async def test_morning_brief_autonomous_with_no_grant_parks(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    """AC3's other half — no matching authority => an approval item, not a send."""
    disable_guard(monkeypatch)
    job = make_job(handler="morning_brief")
    deliverer = _FakeDeliverer(status="delivered")
    handler = MorningBriefHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        scheduler=JobScheduler(db=tmp_db), db=tmp_db, event_bus=EventBus(),
        settings=_brief_settings(),
        proactive_deliverer=deliverer,
        delivery_ledger=_ledger(tmp_db),
    )
    ttoken = TraceContext.start(
        session_key=None, trace_id="t-brief-2", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is False
    assert deliverer.calls == []


def _ledger(db: DbPool) -> Any:
    from stackowl.notifications.delivery_ledger import DeliveryLedger

    return DeliveryLedger(db)
