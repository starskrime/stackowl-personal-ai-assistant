import re
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES
from stackowl.owls.dna import _MUTABLE_TRAITS
from stackowl.owls.evolution import DeltaValidator
from stackowl.owls.dna_storage import _DNA_FIELDS
from stackowl.owls.evolution_limits import DNA_NEUTRAL
from stackowl.owls.dna_hydrator import _SELECT_ALL_DNA

_EXPECTED = ("challenge_level", "verbosity", "curiosity", "formality", "creativity", "precision")


def test_canonical_traits_and_neutral():
    assert TRAIT_NAMES == _EXPECTED
    assert len(TRAIT_NAMES) == 6
    assert NEUTRAL == 0.5


def test_all_live_sites_equal_canonical_in_order():
    assert tuple(_MUTABLE_TRAITS) == _EXPECTED
    assert tuple(_DNA_FIELDS) == _EXPECTED
    assert frozenset(DeltaValidator._TRAITS) == frozenset(_EXPECTED)
    assert DNA_NEUTRAL == NEUTRAL == 0.5


def test_sql_column_order_matches_canonical():
    cols = re.search(r"SELECT\s+(.*?)\s+FROM", _SELECT_ALL_DNA, re.S).group(1)
    names = [c.strip() for c in cols.replace("\n", " ").split(",")]
    traits = tuple(n for n in names if n != "owl_name")
    assert traits == _EXPECTED
