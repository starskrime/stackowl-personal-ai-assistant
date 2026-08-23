"""ESC-38 — resolve what the model typed to the owl it meant.

The fixtures are the REAL live mapping, read from the owls table on 2026-08-23,
rather than invented names — because the defect is specifically that display names
and canonical names diverge, and a fixture that made them agree would test nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.owls.canonical import canonical_owl_name


@dataclass
class _Owl:
    name: str
    display_name: str | None


#: The live mapping, verbatim.
LIVE = [
    _Owl("archivist", "Collector"),
    _Owl("english_tutor", "Professor X"),
    _Owl("headhunter", "Fury"),
    _Owl("hypothesis", "Doom"),
    _Owl("jobmarket", None),
    _Owl("librarian", "Agatha"),
    _Owl("mailbutler", None),
    _Owl("newsdesk", "BuBiLi"),
    _Owl("rca_gatherer", "Widow"),
    _Owl("scout", "Falcon"),
    _Owl("secretary", "Friday"),
    _Owl("syshealth", None),
    _Owl("verifier", "Hawkeye"),
]


# ---------------------------------------------------------------------------
# The orphans that actually exist on disk
# ---------------------------------------------------------------------------

def test_the_two_real_orphans_resolve_to_their_owls() -> None:
    """Collector.md and Falcon.md are orphan FILES whose owls exist."""
    assert canonical_owl_name("Collector", LIVE) == "archivist"
    assert canonical_owl_name("Falcon", LIVE) == "scout"


def test_the_case_variant_orphan_resolves_too() -> None:
    """Both `Falcon.md` AND `falcon.md` exist on disk — the same owl, twice."""
    assert canonical_owl_name("falcon", LIVE) == "scout"
    assert canonical_owl_name("FALCON", LIVE) == "scout"


def test_the_generic_words_resolve_to_NOTHING() -> None:
    """`agent.md`, `owl.md`, `Brain.md` — the model wrote a word, not a name.
    These must NOT resolve, or a fuzzy match would route them somewhere wrong."""
    for junk in ("agent", "owl", "Brain", "assistant", "memory"):
        assert canonical_owl_name(junk, LIVE) is None, junk


def test_a_retired_owl_does_not_resolve() -> None:
    """sysdesign.md / sysfup.md outlived their owls. Silence is the right answer:
    the file is real, the owl is gone, and inventing a mapping would be a guess."""
    assert canonical_owl_name("sysdesign", LIVE) is None
    assert canonical_owl_name("sysfup", LIVE) is None


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------

def test_an_exact_canonical_name_is_returned_unchanged() -> None:
    for owl in LIVE:
        assert canonical_owl_name(owl.name, LIVE) == owl.name


def test_a_canonical_name_beats_another_owls_DISPLAY_name() -> None:
    """The pathological case, and the reason order is specified rather than
    incidental: if one owl is CALLED what another owl is NICKNAMED, the real name
    must win or a write lands in the wrong owl's file."""
    owls = [_Owl("falcon", None), _Owl("scout", "Falcon")]
    assert canonical_owl_name("falcon", owls) == "falcon"


def test_display_name_matching_is_case_insensitive() -> None:
    assert canonical_owl_name("professor x", LIVE) == "english_tutor"
    assert canonical_owl_name("BUBILI", LIVE) == "newsdesk"


def test_owls_without_a_display_name_still_resolve_by_name() -> None:
    for name in ("jobmarket", "mailbutler", "syshealth"):
        assert canonical_owl_name(name, LIVE) == name


# ---------------------------------------------------------------------------
# It must not be clever
# ---------------------------------------------------------------------------

def test_it_is_NOT_fuzzy() -> None:
    """A near-miss resolver routes one owl's notes into another owl's file. An
    orphan loses a write; a mis-resolution corrupts a reader."""
    for near in ("scou", "scouts", "Falco", "secretery", "arch"):
        assert canonical_owl_name(near, LIVE) is None, near


def test_empty_and_whitespace_resolve_to_None() -> None:
    for empty in ("", "   ", None):
        assert canonical_owl_name(empty, LIVE) is None


def test_surrounding_whitespace_is_tolerated() -> None:
    assert canonical_owl_name("  Falcon  ", LIVE) == "scout"


def test_an_empty_registry_resolves_nothing_and_does_not_raise() -> None:
    assert canonical_owl_name("scout", []) is None
