"""PB3 — MorningBriefHandler.execute derives JobResult.success from the rollup.

Same class of bug as check_in: ``success=True`` was hardcoded after computing the
honest ``outcome.rollup``. Interim fix — superseded by the PB6a/6b
verified/effect_class contract.

Story 4.8 (AD-1) — ``_deliver`` now SUBMITS the declared, irreversible
``notifications.deliver_brief`` command instead of calling
``self._job_deliverer.deliver_for_job`` directly, so the rollup a test wants
to script is threaded through a stubbed ``commands/spec/submit.py::
submit_command`` (``stub_submit_command``) rather than a fake job deliverer —
``outcome_from_submission`` is the translation layer under test here, one
door over from where it used to be.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.scheduler.handlers.morning_brief import MorningBriefHandler
from stackowl.scheduler.scheduler import JobScheduler
from tests._story_7_2_helpers import disable_guard, make_job, stub_submit_command

pytestmark = pytest.mark.asyncio


class _StubBridge:
    async def recall(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []

    async def list_staged(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []


def _settings() -> Settings:
    return Settings(brief=BriefSettings(channels=["telegram"]), system=SystemSettings(timezone="UTC"))


def _handler(db: DbPool) -> MorningBriefHandler:
    return MorningBriefHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        scheduler=JobScheduler(db=db),
        db=db,
        event_bus=EventBus(),
        settings=_settings(),
        # Any non-None pair — the REAL deliverer/ledger are never reached
        # once submit_command itself is stubbed; this only needs to satisfy
        # the "_job_deliverer is None" honest-degradation guard.
        proactive_deliverer=object(),  # type: ignore[arg-type]
        delivery_ledger=object(),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("rollup", "expected_success"),
    [("delivered", True), ("failed", False)],
)
async def test_success_follows_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool, rollup: str, expected_success: bool
) -> None:
    disable_guard(monkeypatch)
    calls = stub_submit_command(monkeypatch, rollup=rollup, success=(rollup == "delivered"))
    handler = _handler(tmp_db)

    result = await handler.execute(make_job(handler="morning_brief"))

    assert result.success is expected_success
    assert len(calls) == 1
    assert calls[0]["command_type"] == "notifications.deliver_brief"
