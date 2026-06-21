"""Unit tests for the sub-command metadata layer (commands/metadata.py)."""

from __future__ import annotations

from stackowl.commands.metadata import (
    Arg,
    CommandMeta,
    Example,
    SubCommand,
    render_usage,
    resolve_path,
)


def test_default_meta_is_empty() -> None:
    meta = CommandMeta()
    assert meta.is_empty()
    assert meta.grammar == "verb"
    assert meta.subcommands == ()


def test_arg_render_required_optional_repeat_choices() -> None:
    assert Arg("query").render() == "<query>"
    assert Arg("path", required=False).render() == "[path]"
    assert Arg("file", repeat=True).render() == "<file...>"
    assert Arg("scope", choices=("chat", "global")).render() == "<scope: chat|global>"
    assert Arg("flag", required=False, choices=("a", "b")).render() == "[flag: a|b]"


def test_subcommand_arg_signature() -> None:
    sub = SubCommand(
        name="search",
        summary="Find facts by meaning",
        args=(Arg("query"), Arg("--scope", required=False, choices=("chat", "global"))),
    )
    assert sub.arg_signature() == "<query> [--scope: chat|global]"


def test_resolve_path_single_level() -> None:
    subs = (SubCommand("export", "Export the log"),)
    assert resolve_path(subs, ["export"]) is subs[0]
    assert resolve_path(subs, ["nope"]) is None
    assert resolve_path(subs, []) is None


def test_resolve_path_is_case_insensitive_and_alias_aware() -> None:
    subs = (SubCommand("forget", "Delete a fact", aliases=("rm", "delete")),)
    assert resolve_path(subs, ["FORGET"]) is subs[0]
    assert resolve_path(subs, ["rm"]) is subs[0]


def test_resolve_path_two_levels() -> None:
    """The /browser profile list case — N-level via the same code."""
    leaf = SubCommand("list", "List profiles")
    profile = SubCommand("profile", "Manage profiles", children=(leaf,))
    subs = (profile,)
    assert resolve_path(subs, ["profile"]) is profile
    assert resolve_path(subs, ["profile", "list"]) is leaf
    assert resolve_path(subs, ["profile", "bogus"]) is None


def test_render_usage_verb_lists_subcommands() -> None:
    meta = CommandMeta(
        subcommands=(
            SubCommand("export", "Export the audit log", args=(Arg("--output", required=False),)),
        )
    )
    out = render_usage("audit", meta)
    assert "Usage: /audit <subcommand> [args]" in out
    assert "export [--output]" in out
    assert "Export the audit log" in out


def test_render_usage_marks_nodes_with_children() -> None:
    meta = CommandMeta(
        subcommands=(
            SubCommand("profile", "Manage profiles", children=(SubCommand("list", "List"),)),
        )
    )
    out = render_usage("browser", meta)
    assert "›" in out  # the has-children marker


def test_render_usage_flag_grammar_shows_arg_signature_not_subcommands() -> None:
    meta = CommandMeta(
        grammar="flag",
        args=(Arg("start"), Arg("end"), Arg("--category", required=False, summary="name")),
    )
    out = render_usage("quiet", meta)
    assert "Usage: /quiet <start> <end> [--category]" in out
    assert "subcommand" not in out.lower()


def test_example_holds_invocation_and_note() -> None:
    ex = Example(invocation="/memory search foo", note="recall it")
    assert ex.invocation.startswith("/")
    assert ex.note == "recall it"
