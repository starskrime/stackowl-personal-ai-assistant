"""PB3 — MorningBriefHandler.execute derives JobResult.success from the rollup.

Same class of bug as check_in: ``success=True`` was hardcoded after computing the
honest ``outcome.rollup``. Interim fix — superseded by the PB6a/6b
verified/effect_class contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
from stackowl.scheduler.scheduler import JobScheduler
from tests._story_7_2_helpers import disable_guard, make_job

pytestmark = pytest.mark.asyncio


class _FakeJobDeliverer:
    def __init__(self, rollup: str) -> None:
        self._rollup = rollup

    async def deliver_for_job(self, job: Any, *, message: str, category: str, **_kw: Any) -> Any:
        return ProactiveDeliveryOutcome(rollup=self._rollup)


class _StubBridge:
    async def recall(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []

    async def list_staged(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []


def _settings() -> Settings:
    return Settings(brief=BriefSettings(channels=["telegram"]), system=SystemSettings(timezone="UTC"))


def _handler(db: DbPool, rollup: str) -> MorningBriefHandler:
    handler = MorningBriefHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        scheduler=JobScheduler(db=db),
        db=db,
        event_bus=EventBus(),
        settings=_settings(),
    )
    handler._job_deliverer = _FakeJobDeliverer(rollup)  # type: ignore[assignment]
    return handler


@pytest.mark.parametrize(
    ("rollup", "expected_success"),
    [("delivered", True), ("failed", False)],
)
async def test_success_follows_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool, rollup: str, expected_success: bool
) -> None:
    disable_guard(monkeypatch)
    handler = _handler(tmp_db, rollup)

    result = await handler.execute(make_job(handler="morning_brief"))

    assert result.success is expected_success
