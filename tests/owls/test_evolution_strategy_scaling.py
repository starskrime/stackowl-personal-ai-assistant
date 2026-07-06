"""Task 2 — evolution_strategy scales the finalized per-trait deltas."""
from stackowl.owls.evolution import _scale_deltas


def test_conservative_halves() -> None:
    assert _scale_deltas({"curiosity": 0.2}, "conservative") == {"curiosity": 0.1}


def test_experimental_doubles() -> None:
    assert _scale_deltas({"curiosity": 0.2}, "experimental") == {"curiosity": 0.4}


def test_adaptive_is_unchanged_identity() -> None:
    d = {"curiosity": 0.2}
    assert _scale_deltas(d, "adaptive") is d  # 1× → no new dict allocated


def test_unknown_strategy_is_unchanged_identity() -> None:
    d = {"curiosity": 0.2}
    assert _scale_deltas(d, "bogus") is d
