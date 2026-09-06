"""Metadata contract for /preferences — the fourteenth of fourteen.

WHY THIS FILE EXISTS, and it is the reason D14.1 found anything at all.

The rule "the subcommands declared in meta are exactly the ones handle() actually
implements" is real and enforced — by THIRTEEN hand-written per-command test files.
Measured 2026-09-06: of 34 command classes, 14 declare subcommands (the only ones where
the rule means anything), and 13 of those 14 had a guard. `/preferences` did not.

Nothing had gone wrong because of it — declared and dispatched agree here — which is
precisely why it went unnoticed. A rule kept by remembering to write the file is one case
short the moment someone forgets, and the sibling file
`test_every_command_meta_is_guarded.py` now makes that structural instead of remembered.
"""

from __future__ import annotations

from stackowl.commands.preferences_command import PreferencesCommand

#: What ``handle()`` actually branches on, read off the dispatch:
#:   sub == "remove"          -> _remove
#:   sub in ("list", "")      -> _list      (bare /preferences defaults to list)
#:   anything else            -> unknown-subcommand error + usage
_DISPATCHED = {"list", "remove"}


def _meta():  # type: ignore[no-untyped-def]
    return PreferencesCommand().meta


def test_grammar_is_verb() -> None:
    assert _meta().grammar == "verb"


def test_declared_subcommands_are_exactly_what_handle_dispatches() -> None:
    """No fabricated extras, and nothing dispatchable left undeclared — the same
    honesty guarantee its thirteen peers assert."""
    assert {s.name for s in _meta().subcommands} == _DISPATCHED


def test_remove_declares_the_argument_it_requires() -> None:
    """`handle()` refuses a non-digit and prints usage, so the arg is load-bearing:
    an undeclared one would make the usage text disagree with the parser."""
    remove = next(s for s in _meta().subcommands if s.name == "remove")

    assert [a.name for a in remove.args] == ["n"]


def test_every_subcommand_carries_a_summary() -> None:
    """The summary is what /help and autocomplete render; an empty one shows the
    command as a bare token with no explanation."""
    assert all((s.summary or "").strip() for s in _meta().subcommands)
