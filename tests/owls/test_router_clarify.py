"""Tests for the clarify verdict + line-3 question parse in SecretaryRouter.

Task 2 of the clarify-router-verdict plan:
- `_parse_intent_class` must return 'clarify' as a valid token.
- `_parse_clarify_question(raw, intent_class)` is a new pure helper.
- A 'clarify' verdict with no question text downgrades to 'standard'.
- 2-line (conversational / standard) paths stay byte-identical.
"""

from __future__ import annotations

from stackowl.owls.router import SecretaryRouter


def _r() -> SecretaryRouter:
    # parsing helpers are pure — no registries needed
    return SecretaryRouter.__new__(SecretaryRouter)


def test_parse_clarify_with_question() -> None:
    raw = "secretary\nclarify\nDo you want me to create images, or find existing ones?"
    assert _r()._parse_intent_class(raw) == "clarify"
    assert _r()._parse_clarify_question(raw, "clarify") == \
        "Do you want me to create images, or find existing ones?"


def test_clarify_without_question_downgrades_to_standard() -> None:
    raw = "secretary\nclarify\n"
    assert _r()._parse_clarify_question(raw, "clarify") is None  # no question text


def test_standard_and_conversational_unchanged() -> None:
    assert _r()._parse_intent_class("scout\nstandard") == "standard"
    assert _r()._parse_intent_class("scout\nconversational") == "conversational"
    # no question for non-clarify classes
    assert _r()._parse_clarify_question("scout\nstandard", "standard") is None
