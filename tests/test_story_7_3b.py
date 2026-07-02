"""Story 7.3 — Morning brief handler, command, settings, guard (part B).

Companion to :mod:`tests.test_story_7_3`. This file covers the orchestration
+ surface side:

* :class:`MorningBriefHandler.execute()` orchestration, failure isolation,
  ``job_results`` persistence, and ``morning_brief_rendered`` emission
* :class:`BriefSettings` defaults + per-section toggling
* :class:`BriefCommand` happy-path
* :class:`TestModeGuard` enforcement at handler entry
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.brief_command import BriefCommand
from stackowl.config.settings import BriefSettings
from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from stackowl.events.bus import EventBus
from tests._story_7_3_helpers import (
    StubDb,
    StubMemory,
    disable_guard,
    make_handler,
    make_job,
    make_settings,
    make_state,
)


# ---------------------------------------------------------------------------
# 12–16. MorningBriefHandler orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_runs_all_four_assemblers(monkeypatch: pytest.MonkeyPatch) -> None:
    disable_guard(monkeypatch)
    handler = make_handler()
    result = await handler.execute(make_job())
    assert result.success is True
    assert result.metadata["section_count"] == 4


@pytest.mark.asyncio
async def test_execute_failing_assembler_becomes_error_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    mem = StubMemory(recall_exc=RuntimeError("memory bridge unreachable"))
    handler = make_handler(mem=mem)
    result = await handler.execute(make_job())
    assert result.success is True  # whole brief still succeeds
    assert "section_error:memory bridge unreachable" in (result.output or "")


@pytest.mark.asyncio
async def test_execute_emits_morning_brief_rendered_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1/F109 contract: the handler emits ``morning_brief_rendered`` with the
    HONEST per-channel status, NOT a fake ``morning_brief_delivered`` that lied
    'delivered' even when nothing was sent.

    ``make_handler`` wires NO deliverer, so ``_deliver`` returns the honest
    ``undeliverable`` rollup (rendered + recorded, never actually transported) —
    the event must reflect that truth, never a hard-coded ``delivered``.
    """
    disable_guard(monkeypatch)
    bus = EventBus()
    captured: list[Any] = []
    # The fake 'delivered' event was intentionally replaced — assert it is GONE
    # and the honest 'rendered' event fires in its place.
    delivered_captured: list[Any] = []
    bus.subscribe("morning_brief_delivered", lambda p: delivered_captured.append(p))
    bus.subscribe("morning_brief_rendered", lambda p: captured.append(p))
    handler = make_handler(bus=bus)
    await handler.execute(make_job())
    assert delivered_captured == []  # the fake 'delivered' event is gone
    assert len(captured) == 1
    payload = captured[0]
    # No deliverer wired → nothing was sent → honest 'undeliverable', never a
    # dishonest 'delivered'.
    assert payload["status"] == "undeliverable"
    assert payload["per_channel"] == {}
    assert payload["undeliverable"] == []
    assert payload["section_count"] == 4


@pytest.mark.asyncio
async def test_execute_writes_job_results_row(monkeypatch: pytest.MonkeyPatch) -> None:
    disable_guard(monkeypatch)
    db = StubDb()
    handler = make_handler(db=db)
    job = make_job()
    await handler.execute(job)
    inserts = [e for e in db.executes if "INSERT INTO job_results" in e[0]]
    assert len(inserts) == 1
    params = inserts[0][1]
    assert params[0] == job.job_id
    # C1/F109 honest-status contract: the row records the ACTUAL delivery rollup
    # (here ``undeliverable`` — no deliverer wired), not a hard-coded
    # ``completed`` that lied about a send that never happened.
    assert params[2] == "undeliverable"
    assert isinstance(params[3], str) and len(params[3]) > 0


@pytest.mark.asyncio
async def test_execute_returns_job_result_with_section_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    handler = make_handler()
    result = await handler.execute(make_job())
    assert result.metadata.get("section_count") == 4
    assert result.metadata.get("delivery_channels") == ["telegram"]
    assert isinstance(result.metadata.get("rendered_len"), int)


# ---------------------------------------------------------------------------
# 17. BriefCommand.handle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brief_command_handle_invokes_handler_and_returns_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    handler = make_handler()
    cmd = BriefCommand(handler=handler)
    assert cmd.command == "brief"
    out = await cmd.handle("", make_state())
    assert isinstance(out, str)
    assert "DATE_AND_PRIORITIES" in out


# ---------------------------------------------------------------------------
# 18–19. BriefSettings defaults + per-section toggle
# ---------------------------------------------------------------------------


def test_brief_settings_defaults() -> None:
    s = BriefSettings()
    assert s.schedule == "daily@08:00"
    assert s.channels == ["telegram"]
    for key in (
        "date_and_priorities",
        "memory_highlights",
        "pending_staged",
        "agent_status",
    ):
        assert s.sections[key] is True


@pytest.mark.asyncio
async def test_section_omitted_when_setting_false(monkeypatch: pytest.MonkeyPatch) -> None:
    disable_guard(monkeypatch)
    settings = make_settings(
        sections={
            "date_and_priorities": True,
            "memory_highlights": False,  # explicitly disabled
            "pending_staged": True,
            "agent_status": True,
        }
    )
    handler = make_handler(settings=settings)
    result = await handler.execute(make_job())
    # Disabled section never appears in rendered output
    assert "MEMORY_HIGHLIGHTS" not in (result.output or "")
    # Other sections still present
    assert "DATE_AND_PRIORITIES" in (result.output or "")
    assert "AGENT_STATUS" in (result.output or "")


# ---------------------------------------------------------------------------
# 20. TestModeGuard enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_raises_when_test_mode_active() -> None:
    TestModeGuard.activate()
    try:
        handler = make_handler()
        with pytest.raises(TestModeViolation):
            await handler.execute(make_job())
    finally:
        TestModeGuard.deactivate()
