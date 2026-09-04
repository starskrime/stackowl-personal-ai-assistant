"""The map's description of OUR side is a scheduling input, and it ages.

The REFERENCE_MAP's "**StackOwl.**" line is a 2026-07 snapshot of this tree, and
items are chosen, sequenced and sized from it. In one week TWO entries were found
stale by accident, each while working the item it described: D12.3 named a
per-channel `memory_callbacks.py` that does not exist, and D12.5/D02.3 described a
`serialize_prior` gate that had been deleted. Scheduling work on a stale premise
is the same defect as an instruction that says the test suite hangs — the document
is obeyed, the tree has moved on, and nobody re-reads one against the other.

These tests pin the REPORT's behaviour, not the map's contents. The report is
deliberately not a gate: it cannot reliably tell a stale claim from a correct
statement of absence, and this programme has already spent four attempts learning
what an unsound predicate costs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import map_freshness  # noqa: E402


def test_it_finds_a_symbol_that_does_not_exist() -> None:
    """Vacuity control: the check must be able to see an absent name at all."""
    assert map_freshness.is_absent("zzz_no_such_symbol_anywhere_42")


def test_a_module_is_not_absent_even_though_it_has_no_def() -> None:
    """A first version called these three missing and all three exist.

    `inflight_router`, `git_tool` and `tool_presets` are MODULE names: they appear
    in import statements, never as a `def` or `class`, so a definition-only check
    reports them gone while the code imports them daily.
    """
    for module in ("inflight_router", "git_tool", "tool_presets"):
        assert not map_freshness.is_absent(module), module


def test_a_sql_table_is_not_absent() -> None:
    """`conversations` is defined in a migration, not in Python."""
    assert not map_freshness.is_absent("conversations")


def test_a_sentence_that_DENIES_the_symbol_is_not_flagged() -> None:
    """The distinction that makes this report usable rather than pure noise.

    Fixing a stale entry KEEPS the name and adds the denial, so without this the
    report would fire on every entry the moment it was corrected — 100% false
    positives, which is how a report becomes the thing nobody reads.
    """
    denied = "The blocking `serialize_prior` gate is GONE, replaced by the intake."
    claimed = "Concurrent handling exists via `serialize_prior` plus the router."
    assert map_freshness._denied_nearby(denied, "serialize_prior")
    assert not map_freshness._denied_nearby(claimed, "serialize_prior")


def test_the_report_never_fails_the_build() -> None:
    """It is a triage aid. A gate built on a prose heuristic would be switched off."""
    assert map_freshness.main() == 0
