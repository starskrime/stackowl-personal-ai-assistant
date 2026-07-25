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


def test_cost_meta_declares_usable_subcommands() -> None:
    """Every declared subcommand is usable, and `privacy` stays reachable.

    Deliberately NOT ``names == {"privacy"}``. Freezing the set turns every
    legitimate new subcommand into a CI failure while adding no behavioural
    coverage — the change-detector anti-pattern (map item D18.6). It fired on
    ``/cost turns``, a correct feature, which is exactly the cost such a test
    imposes.

    What actually matters, and is asserted here: `privacy` must remain
    reachable (it is destructive and has a confirmation contract), every
    declared subcommand must render in help, and no two may share a name.
    """
    cmd = CostCommand()
    names = [s.name for s in cmd.meta.subcommands]
    assert "privacy" in names, "the destructive wipe must stay reachable"
    assert len(names) == len(set(names)), f"duplicate subcommand names: {names}"
    assert all(s.summary for s in cmd.meta.subcommands), (
        "a subcommand with no summary is invisible in /help"
    )
    assert cmd.meta.grammar == "verb"
    assert cmd.meta.group == "Cost & Usage"


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
