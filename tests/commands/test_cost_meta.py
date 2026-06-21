"""/cost sub-command metadata — declares only `privacy`; bare /cost stays a summary."""

from __future__ import annotations

import pytest

from stackowl.commands.cost_command import CostCommand


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


def test_cost_declares_only_privacy_subcommand() -> None:
    cmd = CostCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == {"privacy"}
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Cost & Usage"
    assert cmd.meta.subcommands[0].summary


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """An unknown sub shows auto-usage listing `privacy`."""
    out = await CostCommand().handle("frobby", _state())
    assert "Usage: /cost" in out
    assert "privacy" in out


@pytest.mark.asyncio
async def test_bare_cost_does_not_show_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare /cost keeps the spend summary — not the usage block."""

    async def _fake_summary(self: CostCommand) -> str:  # type: ignore[no-untyped-def]
        return "Spend for today: $0.0000 (0 calls)"

    monkeypatch.setattr(CostCommand, "_summary", _fake_summary)
    out = await CostCommand().handle("", _state())
    assert "Usage" not in out
    assert "Spend for today" in out
