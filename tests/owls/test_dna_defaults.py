import re

from stackowl.owls.dna import _MUTABLE_TRAITS
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES
from stackowl.owls.dna_hydrator import _SELECT_ALL_DNA
from stackowl.owls.dna_storage import _DNA_FIELDS
from stackowl.owls.evolution import DeltaValidator
from stackowl.owls.evolution_limits import DNA_NEUTRAL

_EXPECTED = (
    "challenge_level", "verbosity", "curiosity", "formality", "creativity",
    "precision", "completion_drive",
)


def test_canonical_traits_and_neutral():
    assert TRAIT_NAMES == _EXPECTED
    assert len(TRAIT_NAMES) == 7
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


def test_residual_sites_repointed():
    """Guard: all residual dup sites from DRY-cleanup C now reference DnaDefaults."""
    # owls/dna_attribution.py — _MUTABLE_TRAITS, and BandStats.mean_value which
    # took over from the deleted _BAND_CENTERS constant on 2026-09-03. Bands are
    # now derived from the observed distribution, so there is no fixed "mid"
    # centre to point at; the default for an unmeasured band is still NEUTRAL.
    from stackowl.owls.dna_attribution import _MUTABLE_TRAITS as ATTR_TRAITS
    from stackowl.owls.dna_attribution import BandStats
    from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES

    assert tuple(ATTR_TRAITS) == TRAIT_NAMES
    assert BandStats(band="mid", n_samples=0, mean_quality=0.0).mean_value == NEUTRAL

    # commands/owls_helpers.py — _DNA_TRAITS
    from stackowl.commands.owls_helpers import _DNA_TRAITS

    assert tuple(_DNA_TRAITS) == TRAIT_NAMES

    # tui/widgets/constellation_helpers.py — _NEUTRAL_TRAIT
    from stackowl.tui.widgets.constellation_helpers import _NEUTRAL_TRAIT

    assert _NEUTRAL_TRAIT == NEUTRAL
