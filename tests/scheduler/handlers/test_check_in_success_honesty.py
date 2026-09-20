"""PB3 — CheckInHandler.execute derives JobResult.success from the delivery rollup.

Previously hardcoded ``success=True`` even when the honest ``outcome.rollup`` said
otherwise (a ``failed``/``partial`` delivery still reported success and was never
retried, and the ``job_results`` audit table lied). Interim fix — superseded by the
PB6a/6b verified/effect_class contract.

Reuses the real :class:`DateAndPrioritiesAssembler` + a stub memory bridge so
a body always renders and the handler reaches the delivery seam.

Story 4.8 (AD-1) — ``execute`` now SUBMITS the declared, irreversible
``notifications.deliver_check_in`` command instead of calling
``self._job_deliverer.deliver_for_job`` directly, so the rollup a test wants
to script is threaded through a stubbed ``commands/spec/submit.py::
submit_command`` (``stub_submit_command``) rather than a fake job deliverer.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.settings import BriefSettings, Settings, SystemSettings
from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.check_in import CheckInHandler
from stackowl.notifications.proactive_job import job_success_for_rollup
from tests._story_7_2_helpers import disable_guard, make_job, stub_submit_command

pytestmark = pytest.mark.asyncio


class _StubBridge:
    async def recall(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []

    async def list_staged(self, *_a: Any, **_kw: Any) -> list[Any]:
        return []


def _settings() -> Settings:
    return Settings(brief=BriefSettings(channels=["telegram"]), system=SystemSettings(timezone="UTC"))


def _handler(db: DbPool) -> CheckInHandler:
    return CheckInHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        db=db,
        settings=_settings(),
        # Any non-None pair — the REAL deliverer/ledger are never reached
        # once submit_command itself is stubbed; this only needs to satisfy
        # the "_job_deliverer is None" honest-degradation guard.
        proactive_deliverer=object(),  # type: ignore[arg-type]
        delivery_ledger=object(),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("rollup", "expected_success"),
    [("delivered", True), ("failed", False), ("undeliverable", True)],
)
async def test_success_follows_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool, rollup: str, expected_success: bool
) -> None:
    disable_guard(monkeypatch)
    calls = stub_submit_command(monkeypatch, rollup=rollup, success=job_success_for_rollup(rollup))
    handler = _handler(tmp_db)

    result = await handler.execute(make_job(handler="check_in"))

    assert result.success is expected_success
    assert len(calls) == 1
    assert calls[0]["command_type"] == "notifications.deliver_check_in"


async def test_undeliverable_keeps_honest_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_db: DbPool
) -> None:
    disable_guard(monkeypatch)
    stub_submit_command(monkeypatch, rollup="undeliverable", success=True)
    handler = _handler(tmp_db)

    result = await handler.execute(make_job(handler="check_in"))

    assert result.success is True
    assert result.metadata["delivery_status"] == "skipped"
    assert result.metadata["undeliverable"] == []


async def test_skip_reason_distinguishes_no_db_from_no_deliverer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review fix — `_db`/`_job_deliverer` are independently-optional; the
    skip reason must not mislabel a missing db pool as 'no_deliverer'."""
    disable_guard(monkeypatch)
    # db=None with a non-empty rendered body forces the branch under test
    # (today's real _assemble_body always returns "" when db is None, since
    # its one assembler needs a db — patched here so the "no_db" branch,
    # correct-but-currently-unreached through that coupling, is exercised).
    handler = CheckInHandler(
        memory_bridge=_StubBridge(),  # type: ignore[arg-type]
        db=None,
        settings=_settings(),
        proactive_deliverer=object(),  # type: ignore[arg-type]
        delivery_ledger=object(),  # type: ignore[arg-type]
    )

    async def _fake_body(job: Any) -> str:
        return "a rendered body"

    monkeypatch.setattr(handler, "_assemble_body", _fake_body)

    result = await handler.execute(make_job(handler="check_in"))

    assert result.metadata["reason"] == "no_db"
