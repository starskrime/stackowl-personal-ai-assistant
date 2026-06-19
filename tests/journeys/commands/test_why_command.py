"""Dispatch test — /why is wired through CommandRegistry."""
from __future__ import annotations
import pytest
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandNotFoundError, CommandRegistry
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


async def test_why_dispatch_returns_pipeline_step() -> None:
    from stackowl.pipeline.state import PipelineState
    register_all_commands(CommandDeps(), registry=CommandRegistry.instance())
    state = PipelineState(
        trace_id="t",
        session_id="s",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="react_loop",
    )
    result = await CommandRegistry.instance().dispatch("why", "", state)
    assert "pipeline step" in result.lower()


async def test_why_not_found_when_not_registered() -> None:
    with pytest.raises(CommandNotFoundError):
        await CommandRegistry.instance().dispatch("why", "", make_state())
