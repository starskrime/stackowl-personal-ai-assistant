"""A skill's identity key must not erase every script but Latin.

EARNED 2026-08-31, working ESC-73 ("normalise for identity only"). The
normaliser already existed — ``standard.canonical_key`` splits, lowercases and
sorts tokens, ``base_name`` strips ``-N`` first, and ``synthesizer`` already
looks a proposed name up by that key at mint time. Measuring found 0 collisions
in the live ``skills`` table, so the mechanism works.

Then measuring it on non-Latin input found this:

    'Почта проверить'  ->  ''
    'Погода сегодня'   ->  ''
    '日報作成'          ->  ''
    '週報作成'          ->  ''
    'Şəkil çək'        ->  'k kil'

``_CANONICAL_SPLIT_RE`` was ``[^a-z0-9]+`` — every character outside ASCII is
treated as a SEPARATOR, so a name written in Cyrillic, CJK or with Azerbaijani
letters loses its content entirely. Four unrelated skills share one key.

THAT IS THE FAILURE THE FUNCTION'S OWN DOCSTRING FORBIDS: "a near-miss would
merge two skills that are genuinely different, and a wrong merge corrupts a
reader where a duplicate only wastes a row." The synthesizer matches
``by_canon.get(canonical_key(proposed))`` before minting, so on a non-Latin name
it would find the FIRST such skill ever created and treat every later one as
that same skill.

And it is reachable: ``validate_name`` returns ZERO violations for
'Почта-проверить' and 'şəkil-çək'. The platform permits these names and then
collapses them.

[[feedback_no_hardcoded_english]] / [[feedback_cross_platform]] — the rule was
already written down; the regex predates its application here.
"""

from __future__ import annotations

import pytest

from stackowl.skills.standard import base_name, canonical_key

# =========================================================================== #
# 1. The defect
# =========================================================================== #


@pytest.mark.parametrize(
    "name",
    [
        "Почта проверить",
        "Погода сегодня",
        "日報作成",
        "週報作成",
        "Şəkil çək",
        "Xəbər oxu",
        "καθημερινή αναφορά",
    ],
)
def test_a_non_latin_name_keeps_an_identity(name: str) -> None:
    assert canonical_key(name), f"{name!r} canonicalised to nothing — it has no identity"


def test_unrelated_non_latin_skills_are_not_the_same_skill() -> None:
    """The wrong merge, stated directly. All four used to key to ''."""
    names = ["Почта проверить", "Погода сегодня", "日報作成", "週報作成"]
    keys = [canonical_key(n) for n in names]
    assert len(set(keys)) == len(names), (
        f"unrelated skills share an identity: {dict(zip(names, keys, strict=True))}"
    )


def test_azerbaijani_letters_are_not_dropped() -> None:
    """'Şəkil çək' used to become 'k kil' — ş, ə and ç treated as separators."""
    key = canonical_key("Şəkil çək")
    assert "ə" in key or "şəkil" in key, f"letters were stripped from the key: {key!r}"
    assert key != "k kil"


# =========================================================================== #
# 2. Everything it already did, it must keep doing
# =========================================================================== #


def test_separator_variants_still_collapse() -> None:
    """The case the synthesizer's own comment cites, from 2026-08-24."""
    assert canonical_key("incident-evidence-brief") == canonical_key(
        "incident_evidence_brief"
    )
    assert canonical_key("check-stock-price-today") == canonical_key(
        "check_stock_price_today"
    )


def test_word_order_permutations_still_collapse() -> None:
    assert canonical_key("email-check") == canonical_key("check-email")


def test_case_still_folds() -> None:
    assert canonical_key("Daily-Report") == canonical_key("daily-report")


def test_the_numbered_suffix_rule_still_composes() -> None:
    """base_name is applied first, so foo-2 and foo stay one family."""
    assert canonical_key("daily-report-2") == canonical_key("daily-report")
    assert base_name("daily-report-2") == "daily-report"


def test_genuinely_different_latin_skills_stay_different() -> None:
    assert canonical_key("send-email") != canonical_key("read-email")


# =========================================================================== #
# 3. An empty key is not an identity
# =========================================================================== #


def test_a_name_with_no_content_yields_no_key() -> None:
    """Punctuation only. It must return "" rather than a key that matches things.

    Callers must treat "" as "no opinion" — matching on it is how four skills
    became one.
    """
    assert canonical_key("---") == ""
    assert canonical_key("") == ""
