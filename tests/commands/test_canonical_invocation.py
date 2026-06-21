"""Tests for canonical_invocation — reduce a dispatched command to its path."""

from __future__ import annotations

from stackowl.commands.metadata import Arg, CommandMeta, SubCommand
from stackowl.commands.sequence_store import canonical_invocation


def _verb_meta() -> CommandMeta:
    return CommandMeta(
        grammar="verb",
        subcommands=(
            SubCommand(name="remember", summary="store a fact", args=(Arg(name="text"),)),
            SubCommand(name="forget", summary="drop a fact", aliases=("rm",)),
        ),
    )


def _leaf_meta() -> CommandMeta:
    return CommandMeta(grammar="leaf", args=(Arg(name="value"),))


def test_keeps_only_the_subcommand_path() -> None:
    # The freeform fact text after `remember` is NOT part of the canonical path.
    assert (
        canonical_invocation("memory", _verb_meta(), "remember buy milk tomorrow")
        == "/memory remember"
    )


def test_bare_command() -> None:
    assert canonical_invocation("memory", _verb_meta(), "") == "/memory"


def test_resolves_alias_to_real_name() -> None:
    # `rm` is an alias of `forget` → record the canonical name.
    assert canonical_invocation("memory", _verb_meta(), "rm 1234") == "/memory forget"


def test_flag_command_ignores_operands() -> None:
    # A flag/leaf command's operands are args, not a sub-path.
    assert canonical_invocation("tier", _leaf_meta(), "powerful") == "/tier"
