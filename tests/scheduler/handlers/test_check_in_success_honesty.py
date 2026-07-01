"""PB3 — CheckInHandler.execute derives JobResult.success from the delivery rollup.

Previously hardcoded ``success=True`` even when the honest ``outcome.rollup`` said
otherwise (a ``failed``/``partial`` delivery still reported success and was never
retried, and the ``job_results`` audit table lied). Interim fix — superseded by the
PB6a/6b verified/effect_class contract.

Mocks ONLY the job deliverer (a scripted fake), reusing the real
:class:`DateAndPrioritiesAssembler` + a stub memory bridge so a body always
renders and the handler reaches the delivery seam.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.db.pool import DbPool
from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.scheduler.handlers.check_in import CheckInHandler
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


def _handler(db: DbPool, rollup: str) -> CheckInHandler:
    handler = CheckInHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        db=db,
        settings=_settings(),
    )
    handler._job_deliverer = _FakeJobDeliverer(rollup)  # type: ignore[assignment]
    return handler


@pytest.mark.parametrize(
    ("rollup", "expected_success"),
    [("delivered", True), ("failed", False), ("undeliverable", True)],
)
async def test_success_follows_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool, rollup: str, expected_success: bool
) -> None:
    disable_guard(monkeypatch)
    handler = _handler(tmp_db, rollup)

    result = await handler.execute(make_job(handler="check_in"))

    assert result.success is expected_success


async def test_undeliverable_keeps_honest_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool
) -> None:
    disable_guard(monkeypatch)
    handler = _handler(tmp_db, "undeliverable")

    result = await handler.execute(make_job(handler="check_in"))

    assert result.success is True
    assert result.metadata["delivery_status"] == "skipped"
    assert result.metadata["undeliverable"] == []
