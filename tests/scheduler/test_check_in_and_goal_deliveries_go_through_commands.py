"""``notifications.deliver_check_in``/``notifications.deliver_goal_result`` --
Story 4.8's handler-level I/O matrix, plus proof that ``CheckInHandler``/
``GoalExecutionHandler`` submit through the one door instead of calling
``ProactiveJobDeliverer.deliver_for_job`` directly.

Mirrors ``tests/notifications/test_urgent_and_brief_go_through_commands.py``'s
own shape for the morning-brief command.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import stackowl.notifications.commands  # noqa: F401 -- registration side effect
from stackowl.authz.standing_authority import grant
from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.spec.execute import execute_command_task
from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.notifications.commands import DELIVER_CHECK_IN, DELIVER_GOAL_RESULT
from stackowl.notifications.router import Notification
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.scheduler.handlers.check_in import CheckInHandler
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.tools.consent import PRINCIPAL_AUTONOMOUS_SCHEDULER
from tests._story_7_2_helpers import StubBackend, disable_guard, make_job

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


def _ledger(db: DbPool) -> Any:
    from stackowl.notifications.delivery_ledger import DeliveryLedger

    return DeliveryLedger(db)


def _autonomous_trace() -> Any:
    return TraceContext.start(
        session_key=None, trace_id="t-autonomous", interactive=False, channel=None,
        principal=PRINCIPAL_AUTONOMOUS_SCHEDULER,
    )


# --------------------------------------------------------------------------- handler-level


def _check_in_task(command_id: str, job: Any, *, message: str = "hi") -> DurableTask:
    payload = {"job": json.loads(job.model_dump_json()), "message": message, "category": "check_in"}
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{DELIVER_CHECK_IN}", status="running",
        kind="command", command_type=DELIVER_CHECK_IN,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="autonomous", gate_verdict="approved",
    )


def _goal_result_task(command_id: str, job: Any, *, message: str = "the answer") -> DurableTask:
    payload = {
        "job": json.loads(job.model_dump_json()), "message": message,
        "category": "goal_answer", "urgency": "normal",
    }
    return DurableTask(
        task_id=f"cmd-{command_id}", goal=f"command:{DELIVER_GOAL_RESULT}", status="running",
        kind="command", command_type=DELIVER_GOAL_RESULT,
        command_payload=json.dumps(payload),
        command_id=command_id, requester_kind="autonomous", gate_verdict="approved",
    )


async def test_deliver_check_in_handler_delivers(tmp_db: DbPool) -> None:
    job = make_job(
        handler="check_in", target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_check_in_task("cmd-ci-1", job))
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["rollup"] == "delivered"
    assert len(deliverer.calls) == 1


async def test_deliver_check_in_re_run_does_not_resend(tmp_db: DbPool) -> None:
    job = make_job(
        handler="check_in", target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        first = await execute_command_task(_check_in_task("cmd-ci-reclaim", job))
        second = await execute_command_task(_check_in_task("cmd-ci-reclaim", job))
    finally:
        reset_services(token)
    assert first.success is True
    assert second.success is False  # a replay does not reassert a confirmed delivery
    assert second.result.get("rollup") == "unknown"
    assert len(deliverer.calls) == 1


async def test_deliver_goal_result_handler_delivers(tmp_db: DbPool) -> None:
    job = make_job(
        handler="goal_execution", target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    deliverer = _FakeDeliverer(status="delivered")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_goal_result_task("cmd-gr-1", job))
    finally:
        reset_services(token)

    assert outcome.success is True
    assert outcome.result["rollup"] == "delivered"
    assert len(deliverer.calls) == 1
    assert deliverer.calls[0].message == "the answer"


async def test_deliver_goal_result_failed_is_unsuccessful(tmp_db: DbPool) -> None:
    job = make_job(
        handler="goal_execution", target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    deliverer = _FakeDeliverer(status="failed")
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        outcome = await execute_command_task(_goal_result_task("cmd-gr-2", job))
    finally:
        reset_services(token)

    assert outcome.success is False
    assert outcome.result["rollup"] == "failed"


# --------------------------------------------------------------------------- wiring


async def test_check_in_autonomous_with_matching_grant_delivers_without_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    disable_guard(monkeypatch)
    job = make_job(
        handler="check_in", target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    await grant(
        tmp_db, scope_kind="job", scope_id=job.job_id,
        command_type=DELIVER_CHECK_IN, granted_by="autonomous", provenance="seeded",
    )
    deliverer = _FakeDeliverer(status="delivered")
    handler = CheckInHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        db=tmp_db, settings=_brief_settings(),
        proactive_deliverer=deliverer, delivery_ledger=_ledger(tmp_db),
    )
    ttoken = _autonomous_trace()
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is True
    assert len(deliverer.calls) >= 1


async def test_check_in_autonomous_with_no_grant_parks(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    disable_guard(monkeypatch)
    job = make_job(
        handler="check_in", target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    deliverer = _FakeDeliverer(status="delivered")
    handler = CheckInHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        db=tmp_db, settings=_brief_settings(),
        proactive_deliverer=deliverer, delivery_ledger=_ledger(tmp_db),
    )
    ttoken = _autonomous_trace()
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is False
    assert deliverer.calls == []


async def test_goal_execution_autonomous_with_matching_grant_delivers_without_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    disable_guard(monkeypatch)
    backend = StubBackend(response_text="weather: sunny")
    job = make_job(
        handler="goal_execution", params={"goal": "weather"},
        target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    await grant(
        tmp_db, scope_kind="job", scope_id=job.job_id,
        command_type=DELIVER_GOAL_RESULT, granted_by="autonomous", provenance="seeded",
    )
    deliverer = _FakeDeliverer(status="delivered")
    handler = GoalExecutionHandler(backend=backend, db=tmp_db, job_deliverer=object())  # type: ignore[arg-type]
    ttoken = _autonomous_trace()
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is True
    assert len(deliverer.calls) == 1
    assert deliverer.calls[0].message == "weather: sunny"


async def test_goal_execution_autonomous_with_no_grant_parks(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool,
) -> None:
    disable_guard(monkeypatch)
    backend = StubBackend(response_text="weather: sunny")
    job = make_job(
        handler="goal_execution", params={"goal": "weather"},
        target_channels=["telegram"], target_addresses={"telegram": 111},
    )
    deliverer = _FakeDeliverer(status="delivered")
    handler = GoalExecutionHandler(backend=backend, db=tmp_db, job_deliverer=object())  # type: ignore[arg-type]
    ttoken = _autonomous_trace()
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))
    try:
        result = await handler.execute(job)
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)

    assert result.success is False
    assert deliverer.calls == []
