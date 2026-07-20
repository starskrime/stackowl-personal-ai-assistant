"""Metadata contract for /tier — verb grammar with real admin subcommands,
plus the bare-tier-name session-preference shortcut documented via examples.

/tier was merged (see tier_command.py's module docstring) from a flag-only
session-preference command into a verb command with real list/menu/add/remove
subcommands PLUS the original bare-tier-name preference shortcut — the two
token vocabularies (tier names vs. subcommand names) never collide, so both
forms are genuinely supported, not just one advertised over the other.
"""

from __future__ import annotations

from stackowl.commands.tier_command import _ADMIN_SUBCOMMANDS, _VALID_TIERS, TierCommand


def _meta():  # type: ignore[no-untyped-def]
    return TierCommand().meta


def test_grammar_is_verb() -> None:
    assert _meta().grammar == "verb"


def test_declares_real_admin_subcommands() -> None:
    """The subcommands declared in meta are exactly the ones handle() actually
    implements — no fake ones (see module docstring's 'honesty guarantee')."""
    names = {s.name for s in _meta().subcommands}
    assert names == _ADMIN_SUBCOMMANDS


def test_admin_subcommands_have_no_fabricated_extras() -> None:
    assert len(_meta().subcommands) == len(_ADMIN_SUBCOMMANDS)


def test_bare_tier_preference_shortcut_is_documented_via_examples() -> None:
    """The verb grammar doesn't render top-level `args`, so the bare-tier-name
    preference shortcut (still fully supported by handle()) must be
    discoverable via meta.examples instead — asserting it's actually there,
    not silently dropped by the merge."""
    invocations = [e.invocation for e in _meta().examples]
    assert any(tier in inv for inv in invocations for tier in _VALID_TIERS), (
        f"No example invocation shows the bare-tier-name shortcut: {invocations!r}"
    )


def test_group() -> None:
    assert _meta().group == "Providers & Routing"
