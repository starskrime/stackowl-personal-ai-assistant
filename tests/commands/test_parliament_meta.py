"""/parliament sub-command metadata — mirrors test_audit_meta.py.

Unlike the other verb commands, /parliament has an intentional topic
fall-through: anything that is not one of the four real sub-commands is treated
as a free-text topic to start a session — NOT an unknown-sub usage error. These
tests assert the metadata declares exactly the four real subs AND that the
topic fall-through is preserved (a non-sub token does NOT return usage).
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import render_usage
from stackowl.commands.parliament_command import ParliamentCommand

_EXPECTED = {"log", "push", "expand", "unsuppress"}


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


def test_parliament_declares_all_subcommands() -> None:
    cmd = ParliamentCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == _EXPECTED
    # The free-text topic is NOT a declared sub-command.
    assert "topic" not in names


def test_parliament_grammar_is_verb() -> None:
    assert ParliamentCommand().meta.grammar == "verb"
    assert ParliamentCommand().meta.group == "Owls"


def test_every_parliament_subcommand_has_nonempty_summary() -> None:
    cmd = ParliamentCommand()
    for sub in cmd.meta.subcommands:
        assert sub.summary.strip(), f"/parliament {sub.name} has a blank summary"


@pytest.mark.asyncio
async def test_free_text_topic_is_not_treated_as_unknown_sub() -> None:
    """A non-sub token starts a session (topic fall-through), not usage.

    With no orchestrator wired the start path returns the not-configured
    message — the important contract is that it does NOT return the
    auto-generated usage block (which would mean the topic fall-through was
    replaced by an unknown-sub branch).
    """
    cmd = ParliamentCommand()  # all deps None
    out = await cmd.handle("should we migrate the database", _state())
    assert out != render_usage("parliament", cmd.meta)
    assert "Usage:" not in out
