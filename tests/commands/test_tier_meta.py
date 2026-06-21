"""Metadata contract for /tier — flag grammar, no fake subcommands."""

from __future__ import annotations

from stackowl.commands.tier_command import TierCommand


def _meta():  # type: ignore[no-untyped-def]
    return TierCommand().meta


def test_grammar_is_flag() -> None:
    assert _meta().grammar == "flag"


def test_declares_no_fake_subcommands() -> None:
    assert _meta().subcommands == ()


def test_args_declared() -> None:
    args = _meta().args
    assert [a.name for a in args] == ["tier"]
    assert args[0].choices == ("fast", "standard", "powerful", "local")
    assert args[0].required is False


def test_group() -> None:
    assert _meta().group == "Providers & Routing"
