"""Layer 1 — pure, exhaustive coverage of :func:`parse_completion`.

The completion-context parser is the brain of context-aware sub-command
autocomplete: from a raw input buffer it decides whether we are completing a
top-level command, a sub-command (N-level via ``resolve_path``), or nothing
(past the args, a flag-grammar command, or an unknown command).  These cases
carry the breadth — the state/pilot layers carry integration truth.
"""

from __future__ import annotations

import pytest

from stackowl.commands.metadata import (
    Arg,
    CommandMeta,
    SubCommand,
)
from stackowl.tui.widgets.compose_helpers import (
    CommandInfo,
    CompletionLevel,
    parse_completion,
)

pytestmark = pytest.mark.tui


# A faithful slice of the real shipped surface — /memory (8 verb subs), /browser
# (2-level verb tree), /quiet (flag grammar). Built as CommandInfo carrying
# CommandMeta exactly like the startup snapshot does.
_MEMORY_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand("stats", "Show memory stats"),
        SubCommand("search", "Search facts", args=(Arg("query"),)),
        SubCommand("delete", "Delete a fact", args=(Arg("fact_id"),)),
        SubCommand("budget", "Show memory budget"),
        SubCommand("reindex", "Rebuild the index"),
        SubCommand("remember", "Store a fact", args=(Arg("text"),)),
        SubCommand("forget", "Forget a fact", args=(Arg("fact_id"),)),
        SubCommand("export", "Export memory"),
    ),
)

_BROWSER_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand(
            "profile",
            "Manage browser profiles",
            children=(
                SubCommand("list", "List profiles"),
                SubCommand("delete", "Delete a profile", args=(Arg("name"),)),
            ),
        ),
        SubCommand(
            "watch",
            "Manage watches",
            children=(SubCommand("list", "List watches"),),
        ),
    ),
)

_QUIET_META = CommandMeta(
    grammar="flag",
    args=(Arg("minutes", required=False),),
)

_WHOAMI_META = CommandMeta(grammar="leaf")

# A faithful slice of /provider's shape: a verb command whose LEAF sub-command
# ("add") takes a free-text arg, then a choice-backed arg, then an optional
# free-text arg — exactly the case that was silently swallowed before this fix.
_SVC_META = CommandMeta(
    grammar="verb",
    subcommands=(
        SubCommand(
            "add",
            "Register a new service",
            args=(
                Arg("name"),
                Arg("protocol", choices=("openai", "anthropic")),
                Arg("note", required=False),
            ),
        ),
    ),
)

_TAG_META = CommandMeta(grammar="flag", args=(Arg("tag", repeat=True),))


def _infos() -> list[CommandInfo]:
    return [
        CommandInfo("memory", "Memory management", meta=_MEMORY_META),
        CommandInfo("browser", "Browser control", meta=_BROWSER_META),
        CommandInfo("quiet", "Mute notifications", meta=_QUIET_META),
        CommandInfo("whoami", "Show identity", meta=_WHOAMI_META),
        CommandInfo("memorize", "Decoy prefix-overlap", meta=CommandMeta(grammar="leaf")),
        CommandInfo("svc", "Service management", meta=_SVC_META),
        CommandInfo("tag", "Add tags", meta=_TAG_META),
    ]


def _names(ctx) -> list[str]:  # type: ignore[no-untyped-def]
    return [c.name for c in ctx.candidates]


# ---------------------------------------------------------------------------
# Command-level (no settled command yet)
# ---------------------------------------------------------------------------


def test_partial_command_is_command_level() -> None:
    # At command level the parser only reports the level + partial; top-level
    # name filtering stays in the existing filter_command_infos path (the SUB
    # candidate list is reserved for sub-commands).
    ctx = parse_completion("/me", _infos())
    assert ctx.level is CompletionLevel.COMMAND
    assert ctx.partial == "me"
    assert ctx.candidates == ()


def test_bare_slash_lists_all_commands() -> None:
    ctx = parse_completion("/", _infos())
    assert ctx.level is CompletionLevel.COMMAND
    assert ctx.partial == ""
    assert ctx.candidates == ()


def test_empty_buffer_is_none() -> None:
    ctx = parse_completion("", _infos())
    assert ctx.level is CompletionLevel.NONE
    assert ctx.candidates == ()


def test_non_slash_buffer_is_none() -> None:
    ctx = parse_completion("hello", _infos())
    assert ctx.level is CompletionLevel.NONE


# ---------------------------------------------------------------------------
# Sub-level (command settled, followed by a space)
# ---------------------------------------------------------------------------


def test_settled_command_space_lists_all_subcommands() -> None:
    ctx = parse_completion("/memory ", _infos())
    assert ctx.level is CompletionLevel.SUB
    assert ctx.command == "memory"
    assert len(ctx.candidates) == 8
    assert _names(ctx) == [
        "stats",
        "search",
        "delete",
        "budget",
        "reindex",
        "remember",
        "forget",
        "export",
    ]
    assert ctx.partial == ""


def test_partial_sub_token_prefix_filters() -> None:
    ctx = parse_completion("/memory st", _infos())
    assert ctx.level is CompletionLevel.SUB
    assert _names(ctx) == ["stats"]
    assert ctx.partial == "st"


def test_sub_with_children_flags_has_children() -> None:
    ctx = parse_completion("/browser ", _infos())
    assert ctx.level is CompletionLevel.SUB
    names = _names(ctx)
    assert "profile" in names
    assert "watch" in names
    by_name = {c.name: c for c in ctx.candidates}
    assert by_name["profile"].has_children is True
    assert by_name["watch"].has_children is True


def test_leaf_sub_has_no_children_flag() -> None:
    ctx = parse_completion("/memory ", _infos())
    by_name = {c.name: c for c in ctx.candidates}
    assert by_name["stats"].has_children is False


# ---------------------------------------------------------------------------
# N-level via resolve_path (NO special-casing /browser)
# ---------------------------------------------------------------------------


def test_two_level_descends_into_children() -> None:
    ctx = parse_completion("/browser profile ", _infos())
    assert ctx.level is CompletionLevel.SUB
    assert ctx.command == "browser"
    assert _names(ctx) == ["list", "delete"]


def test_two_level_partial_filters_children() -> None:
    ctx = parse_completion("/browser profile de", _infos())
    assert _names(ctx) == ["delete"]


# ---------------------------------------------------------------------------
# Past args → no candidates
# ---------------------------------------------------------------------------


def test_past_args_yields_no_candidates() -> None:
    ctx = parse_completion("/memory search foo ", _infos())
    assert ctx.level is CompletionLevel.NONE
    assert ctx.candidates == ()


def test_leaf_sub_followed_by_space_is_past_args() -> None:
    # `stats` is a terminal leaf with no children/args — after it there is
    # nothing more to complete.
    ctx = parse_completion("/memory stats ", _infos())
    assert ctx.level is CompletionLevel.NONE
    assert ctx.candidates == ()


# ---------------------------------------------------------------------------
# Honesty: flag/leaf grammar offers NO fake sub rows
# ---------------------------------------------------------------------------


def test_flag_grammar_offers_no_subcommands_but_hints_its_arg() -> None:
    # No fake sub-command rows (candidates stays empty) — but /quiet DOES take
    # a free-text arg (minutes), so it now surfaces as a non-selectable tip
    # per CommandMeta's own documented promise for grammar="flag".
    ctx = parse_completion("/quiet ", _infos())
    assert ctx.level is CompletionLevel.ARG_HINT
    assert ctx.candidates == ()
    assert ctx.arg_hint is not None
    assert ctx.arg_hint.name == "minutes"


def test_leaf_grammar_offers_no_subcommands() -> None:
    # /whoami takes NO args at all → nothing to hint either.
    ctx = parse_completion("/whoami ", _infos())
    assert ctx.level is CompletionLevel.NONE
    assert ctx.candidates == ()


# ---------------------------------------------------------------------------
# Unknown command settled → no candidates
# ---------------------------------------------------------------------------


def test_unknown_command_yields_no_candidates() -> None:
    ctx = parse_completion("/unknowncmd ", _infos())
    assert ctx.level is CompletionLevel.NONE
    assert ctx.candidates == ()


def test_settled_command_carries_metadata_for_tab_descent() -> None:
    # A settled command WITHOUT a trailing space is still command-level (the
    # user may still be typing the name), but exposes whether the exact token
    # is a known verb command so the caller can decide to descend on Tab.
    ctx = parse_completion("/memory", _infos())
    assert ctx.level is CompletionLevel.COMMAND
    assert ctx.settled_verb == "memory"


def test_partial_command_not_exact_has_no_settled_verb() -> None:
    ctx = parse_completion("/mem", _infos())
    assert ctx.level is CompletionLevel.COMMAND
    assert ctx.settled_verb is None


# ---------------------------------------------------------------------------
# ARG_HINT — a leaf sub-command's (or flag command's) positional args, which
# were previously swallowed entirely (level NONE, no tip at all).
# ---------------------------------------------------------------------------


def test_leaf_sub_free_text_arg_is_arg_hint() -> None:
    ctx = parse_completion("/svc add ", _infos())
    assert ctx.level is CompletionLevel.ARG_HINT
    assert ctx.candidates == ()
    assert ctx.arg_hint is not None
    assert ctx.arg_hint.name == "name"


def test_leaf_sub_choice_arg_is_selectable_sub() -> None:
    # The 2nd arg (protocol) HAS choices → reused as selectable SUB rows,
    # exactly like sub-command names, not a silent/hint-only dead end.
    ctx = parse_completion("/svc add myname ", _infos())
    assert ctx.level is CompletionLevel.SUB
    assert _names(ctx) == ["openai", "anthropic"]


def test_leaf_sub_choice_arg_prefix_filters() -> None:
    ctx = parse_completion("/svc add myname op", _infos())
    assert ctx.level is CompletionLevel.SUB
    assert _names(ctx) == ["openai"]
    assert ctx.partial == "op"


def test_leaf_sub_optional_trailing_arg_still_hints() -> None:
    ctx = parse_completion("/svc add myname openai ", _infos())
    assert ctx.level is CompletionLevel.ARG_HINT
    assert ctx.arg_hint is not None
    assert ctx.arg_hint.name == "note"


def test_leaf_sub_past_all_declared_args_is_none() -> None:
    ctx = parse_completion("/svc add myname openai a-note extra ", _infos())
    assert ctx.level is CompletionLevel.NONE


def test_repeating_arg_keeps_hinting_after_first_value() -> None:
    ctx = parse_completion("/tag a b ", _infos())
    assert ctx.level is CompletionLevel.ARG_HINT
    assert ctx.arg_hint is not None
    assert ctx.arg_hint.name == "tag"
