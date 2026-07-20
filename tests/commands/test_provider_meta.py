"""/provider sub-command metadata — mirrors test_audit_meta.py.

Asserts the declared metadata matches the real if/elif dispatch ladder and that
an unknown sub-command surfaces the auto-generated usage block (not silent).
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.provider_command import ProviderCommand

_EXPECTED = {
    "list", "add", "remove", "set-tier", "edit", "enable", "disable", "set-token",
    "rename", "status",
}


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


def test_provider_declares_all_subcommands() -> None:
    cmd = ProviderCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED


def test_provider_grammar_is_verb() -> None:
    assert ProviderCommand().meta.grammar == "verb"
    assert ProviderCommand().meta.group == "Providers & Routing"


def test_every_provider_subcommand_has_nonempty_summary() -> None:
    cmd = ProviderCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip(), f"/provider {sub.name} has a blank summary"


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage() -> None:
    """`/provider bogus` shows the auto-generated usage with every sub listed."""
    cmd = ProviderCommand()
    out = await cmd.handle("bogus whatever", _state())
    assert out == render_usage("provider", cmd.meta)
    for name in _EXPECTED:
        assert name in out
