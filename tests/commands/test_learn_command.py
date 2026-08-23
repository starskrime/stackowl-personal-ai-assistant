"""D09.5 — `/learn` contributes a turn PROMPT, and every other command does not.

The seam (`SlashCommand.build_turn_prompt`) is the one new concept this item adds,
so the tests that matter most are the ones proving it is INERT everywhere it was
not asked for. A hook that quietly changed the behaviour of 30-odd existing
commands would be a second dispatch path wearing a default argument.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.commands.base import SlashCommand
from stackowl.commands.learn_command import LearnCommand
from stackowl.commands.manifest import SHIPPED_COMMANDS
from stackowl.skills.learn_prompt import build_learn_prompt


# ---------------------------------------------------------------------------
# The command itself
# ---------------------------------------------------------------------------

def test_learn_builds_a_turn_prompt() -> None:
    cmd = LearnCommand()
    got = cmd.build_turn_prompt("how we fixed the VPN")
    assert got is not None
    assert got == build_learn_prompt("how we fixed the VPN")


def test_a_bare_learn_still_builds_one() -> None:
    """I3 — `/learn` with no argument means "what we just did"."""
    assert LearnCommand().build_turn_prompt("")


def test_it_is_declared_and_named() -> None:
    cmd = LearnCommand()
    assert cmd.command == "learn"
    assert cmd.description
    assert "learn" in SHIPPED_COMMANDS, (
        "an undeclared command is invisible to the shipped-surface guard"
    )


@pytest.mark.asyncio
async def test_handle_degrades_HONESTLY_on_a_surface_without_the_seam() -> None:
    """A surface that does not honour build_turn_prompt shows the user exactly
    what WOULD have run, rather than claiming work that never happened."""
    out = await LearnCommand().handle("x", None)  # type: ignore[arg-type]
    assert out == build_learn_prompt("x")


# ---------------------------------------------------------------------------
# The seam must be inert by default — this is the load-bearing half
# ---------------------------------------------------------------------------

def test_the_base_default_is_None() -> None:
    """Every command that does not opt in stays an ordinary command."""

    class _Plain(SlashCommand):
        @property
        def command(self) -> str:
            return "plain"

        @property
        def description(self) -> str:
            return "d"

        async def handle(self, args: str, state: object) -> str:  # type: ignore[override]
            return "ok"

    assert _Plain().build_turn_prompt("anything") is None


def test_no_OTHER_shipped_command_overrides_the_seam() -> None:
    """If a second command ever opts in, that must be a deliberate act.

    This does not forbid it — it forbids it happening SILENTLY. A new override
    fails here and whoever added it has to say so.
    """
    import stackowl.commands.assembly as assembly

    overriders: list[str] = []
    for name, obj in vars(assembly).items():
        del name
        if not (inspect.isclass(obj) and issubclass(obj, SlashCommand)):
            continue
        if obj is SlashCommand:
            continue
        if obj.build_turn_prompt is not SlashCommand.build_turn_prompt:
            overriders.append(obj.__name__)
    assert overriders in ([], ["LearnCommand"]), overriders


def test_the_seam_has_exactly_one_meaning() -> None:
    """It returns TEXT or None. Not a response, not an action, not a callable —
    anything richer becomes the general 'commands may do anything' hook that the
    design explicitly rules out."""
    sig = inspect.signature(SlashCommand.build_turn_prompt)
    assert list(sig.parameters) == ["self", "args"]
    got = LearnCommand().build_turn_prompt("x")
    assert isinstance(got, str)
