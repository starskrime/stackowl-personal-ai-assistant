"""When the platform knows why it failed, it must say so — not "check the logs".

WHAT HE RECEIVES TODAY, verbatim from the live archive:

    ⚠ I hit a problem completing this and couldn't finish; the technical detail
      is in the logs. [AllProvidersUnavailableError]

MEASURED 2026-09-03 across every failed turn on his own Telegram channel:

    telegram failures                     5,739
    told to "check the logs"              3,856   (67%)

    AllProvidersUnavailableError          2,115
    OwlTimeoutError                       1,800
    CircuitOpenError                      1,258
    ProviderError                           211
                                        -------
                                          5,384   (94% of them)

Every one of those four is an infrastructure condition with a one-sentence
explanation. He is told none of them. He is told to read logs he cannot reach
from Telegram, and handed a Python exception class in brackets.

THE CAUSE IS IN ONE FUNCTION, AND IT ALREADY HAS THE ANSWER.
``delivery_gate._neutral_fallback`` reads the failure class into ``marker`` and
spends it on a debug bracket, then renders ``self_heal_floor_minimal`` —
the last-resort "we know nothing" prose — for EVERY case::

    marker = classes[0] if classes else "error"
    prose = localize("self_heal_floor_minimal", state.language)
    return f"{_NEUTRAL_PREFIX}{prose} [{marker}]"

So the last-resort message is used for failures that are not a last resort. The
cause was measured, classified, stored in ``task_outcomes.failure_class``, used
to cluster incidents and mine lessons — and then, at the one moment it would
have helped the person waiting, thrown away in favour of "the technical detail
is in the logs".

WHAT STAYS. The bracket keeps the class name: it is genuinely useful for
debugging and costs one word. The generic prose stays for classes we cannot
explain — inventing an explanation for an unknown failure would be the
overclaim this floor exists to prevent. Only a class we can actually name gets
a plain sentence.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.delivery_gate import explain_failure_class

#: The four classes that account for 94% of what reaches him.
MEASURED_TOP_CLASSES = (
    "AllProvidersUnavailableError",
    "OwlTimeoutError",
    "CircuitOpenError",
    "ProviderError",
)


@pytest.mark.parametrize("cls", MEASURED_TOP_CLASSES)
def test_the_classes_he_actually_hits_are_explained(cls: str) -> None:
    """THE REGRESSION. 5,384 of 5,739 failures he saw were one of these."""
    prose = explain_failure_class(cls, "en")
    assert prose, f"{cls} reached him 100s of times with no explanation"
    assert "logs" not in prose.lower(), (
        f"{cls} still sends him to the logs: {prose!r}"
    )


@pytest.mark.parametrize("cls", MEASURED_TOP_CLASSES)
def test_an_explanation_never_leaks_the_class_name_into_the_prose(cls: str) -> None:
    """The bracket carries the class for debugging. The SENTENCE is for him, and
    "AllProvidersUnavailableError" is not a sentence."""
    assert cls not in (explain_failure_class(cls, "en") or "")


def test_an_unknown_class_is_not_given_an_invented_explanation() -> None:
    """The generic prose must survive for genuinely unknown failures. Inventing
    a cause for one is the overclaim this floor exists to prevent — and this
    codebase has already paid for a floor that named a capability it had not
    verified."""
    assert explain_failure_class("SomeNeverSeenError", "en") is None
    assert explain_failure_class("", "en") is None
    assert explain_failure_class(None, "en") is None


@pytest.mark.parametrize("lang", ["en", "de", "fr", "es"])
def test_every_explanation_exists_in_every_language(lang: str) -> None:
    """``localize`` returns the KEY ITSELF when a string is missing, so an
    untranslated explanation would print a bare key at a user. Same guard the
    composed floor already carries."""
    for cls in MEASURED_TOP_CLASSES:
        prose = explain_failure_class(cls, lang)
        assert prose, f"{cls}/{lang} missing"
        assert "floor_cause" not in prose, (
            f"{cls}/{lang} leaked a localization key: {prose!r}"
        )


def test_a_non_english_explanation_is_actually_translated() -> None:
    """A catalogue that silently English-fallbacks would pass the test above
    while shipping English to a German turn."""
    assert explain_failure_class("AllProvidersUnavailableError", "de") != (
        explain_failure_class("AllProvidersUnavailableError", "en")
    )
