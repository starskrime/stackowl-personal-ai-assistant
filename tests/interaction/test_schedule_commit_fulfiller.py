"""ScheduleCommitFulfiller — the do-the-action upgrade to overclaim trigger 4.

A detected text-only scheduling promise should be FULFILLED (a real job minted
through CronjobTool's guarded create path) rather than confessed; the honest
ask-floor stays as the fallback for every degraded path.
"""

from __future__ import annotations

from typing import Any

import pytest

import stackowl.interaction.schedule_commit_fulfiller as mod
from stackowl.interaction.schedule_commit_fulfiller import ScheduleCommitFulfiller
from stackowl.providers.base import CompletionResult
from stackowl.tools.base import ToolResult


class _FakeProvider:
    def __init__(self, canned: str) -> None:
        self._canned = canned
        self.calls = 0

    async def complete(self, messages: Any, model: str, **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content=self._canned, input_tokens=1, output_tokens=1,
            model="fast", provider_name="fake", duration_ms=1.0,
        )


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider | None) -> None:
        self._provider = provider

    def get_by_tier(self, tier: str) -> Any:
        if self._provider is None:
            raise RuntimeError("no fast provider")
        return self._provider


class _FakeCronjobTool:
    created: list[dict[str, Any]] = []
    outcome: ToolResult = ToolResult(success=True, output="created job cron-1", error=None, duration_ms=1.0)

    async def execute(self, **kwargs: Any) -> ToolResult:
        _FakeCronjobTool.created.append(kwargs)
        return _FakeCronjobTool.outcome


@pytest.fixture(autouse=True)
def _patch_cronjob(monkeypatch: pytest.MonkeyPatch):
    _FakeCronjobTool.created = []
    _FakeCronjobTool.outcome = ToolResult(
        success=True, output="created job cron-1", error=None, duration_ms=1.0
    )
    import stackowl.tools.scheduling.cronjob as cronjob_mod

    monkeypatch.setattr(cronjob_mod, "CronjobTool", _FakeCronjobTool)
    yield


@pytest.mark.asyncio
async def test_valid_extraction_creates_job_and_returns_receipt() -> None:
    provider = _FakeProvider('{"goal": "check GOOGL news and report", "schedule": "in 2h"}')
    fulfiller = ScheduleCommitFulfiller(_FakeRegistry(provider))  # type: ignore[arg-type]

    receipt = await fulfiller.fulfill(
        response="Sure — I'll check GOOGL news in 2 hours and report back!",
        request="watch googl news for me",
    )

    assert receipt is not None
    assert "check GOOGL news and report" in receipt
    assert "in 2h" in receipt
    assert _FakeCronjobTool.created == [
        {"action": "create", "prompt": "check GOOGL news and report", "schedule": "in 2h"}
    ]


@pytest.mark.asyncio
async def test_none_verdict_falls_back_without_creating() -> None:
    fulfiller = ScheduleCommitFulfiller(_FakeRegistry(_FakeProvider("NONE")))  # type: ignore[arg-type]
    receipt = await fulfiller.fulfill(response="I'll get to it sometime.", request="x")
    assert receipt is None
    assert _FakeCronjobTool.created == []


@pytest.mark.asyncio
async def test_invalid_schedule_grammar_falls_back_without_creating() -> None:
    fulfiller = ScheduleCommitFulfiller(
        _FakeRegistry(_FakeProvider('{"goal": "ping user", "schedule": "whenever feels right"}'))  # type: ignore[arg-type]
    )
    receipt = await fulfiller.fulfill(response="I'll ping you later!", request="x")
    assert receipt is None
    assert _FakeCronjobTool.created == []


@pytest.mark.asyncio
async def test_cronjob_refusal_falls_back() -> None:
    _FakeCronjobTool.outcome = ToolResult(
        success=False, output="", error="prompt flagged", duration_ms=1.0
    )
    fulfiller = ScheduleCommitFulfiller(
        _FakeRegistry(_FakeProvider('{"goal": "ping user", "schedule": "in 5m"}'))  # type: ignore[arg-type]
    )
    receipt = await fulfiller.fulfill(response="I'll ping you in 5!", request="x")
    assert receipt is None  # tool refused (e.g. security scan) → honest floor path


@pytest.mark.asyncio
async def test_no_provider_falls_back() -> None:
    fulfiller = ScheduleCommitFulfiller(_FakeRegistry(None))  # type: ignore[arg-type]
    receipt = await fulfiller.fulfill(response="I'll ping you in 5!", request="x")
    assert receipt is None
    assert _FakeCronjobTool.created == []


@pytest.mark.asyncio
async def test_garbage_extraction_falls_back() -> None:
    fulfiller = ScheduleCommitFulfiller(_FakeRegistry(_FakeProvider("sure thing boss")))  # type: ignore[arg-type]
    receipt = await fulfiller.fulfill(response="I'll ping you in 5!", request="x")
    assert receipt is None
    assert _FakeCronjobTool.created == []


def test_parse_accepts_fenced_json_anywhere_in_text() -> None:
    goal_schedule = mod.ScheduleCommitFulfiller._parse(
        'Here you go:\n{"goal": "remind about standup", "schedule": "daily@09:00"}'
    )
    assert goal_schedule == ("remind about standup", "daily@09:00")
