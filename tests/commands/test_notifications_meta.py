"""/notifications sub-command metadata — declared subs match the dispatch ladder."""

from __future__ import annotations

import pytest

from stackowl.commands.notifications_command import NotificationsMissedCommand


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_notifications_declares_missed_subcommand() -> None:
    cmd = NotificationsMissedCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == {"missed"}
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Notifications"
    assert cmd.meta.subcommands[0].summary


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    cmd = NotificationsMissedCommand(db=_FakeDb())
    out = await cmd.handle("bogus", _state())
    assert "Usage: /notifications" in out
    assert "missed" in out


class _FakeDb:
    async def fetch_all(self, sql: str, params):  # type: ignore[no-untyped-def]
        return []
