"""PARL-6 (F080) — a near-miss LLM-chosen owl name is fuzzy-corrected, not dropped.

`SecretaryRouter._parse_choice` runs `FuzzyMatcher.find` on a non-exact candidate
and accepts a high-confidence correction (logged) BEFORE collapsing to the
secretary fallback. 'scoutt' must resolve to 'scout', not 'secretary'.
"""

from __future__ import annotations

from stackowl.owls.router import SecretaryRouter


def _router() -> SecretaryRouter:
    # _parse_choice needs no provider/registry I/O — construct with stubs.
    return SecretaryRouter(provider_registry=object(), owl_registry=object())  # type: ignore[arg-type]


def test_near_miss_corrected_to_known_owl() -> None:
    router = _router()
    known = {"scout", "sage", "secretary"}
    assert router._parse_choice("scoutt", known) == "scout"  # noqa: SLF001


def test_exact_match_unchanged() -> None:
    router = _router()
    known = {"scout", "sage", "secretary"}
    assert router._parse_choice("sage", known) == "sage"  # noqa: SLF001


def test_far_miss_still_falls_back_to_secretary() -> None:
    router = _router()
    known = {"scout", "sage", "secretary"}
    # 'zzzzzz' is not a plausible misspelling of any owl → secretary fallback.
    assert router._parse_choice("zzzzzz", known) == "secretary"  # noqa: SLF001


def test_multiline_first_line_then_fuzzy() -> None:
    router = _router()
    known = {"scout", "sage", "secretary"}
    # Owl name parsed from line 1, fuzzy-corrected.
    assert router._parse_choice("scoutt\nstandard", known) == "scout"  # noqa: SLF001
