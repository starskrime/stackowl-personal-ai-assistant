"""One failure class, one explanation — whichever floor renders it.

WHAT HE RECEIVED TONIGHT, three attempts at the same scheduled job, 19:00:25 to
19:00:55, all defeated by the same unreachable provider:

    attempt 1  I couldn't fully complete that. Technical detail: Provider
               'NeraAiRaw' error: Connection error.
    attempt 2  I couldn't fully complete that. Technical detail: Provider
               'NeraAiRaw' error: Connection error.
    attempt 3  ⚠ I could not reach my model just now, so I could not work on
               this. It usually comes back on its own — ask me again in a
               moment. [AllProvidersUnavailableError]

The third message explains what happened and what to do. The first two hand him
"Connection error". Same outage, same minute, same job.

THE CAUSE IS TWO RENDERERS AND ONE CATALOGUE WIRED TO ONE OF THEM.
``delivery_gate._neutral_fallback`` consults :func:`explain_failure_class`;
``supervisor.synthesize_floor`` composes its own sentences and renders
``self_heal_floor_s_error`` with the raw exception text. The plain-language
catalogue was added to the path where the symptom had been REPORTED, without
asking which other paths render a failure to him — fixing the example instead of
the architecture.

MEASURED over the whole archive:

    replies rendering "Technical detail:"                     1,791
    of the top rows, carrying a class we CAN explain          1,529  (86%)
      CircuitOpenError / telegram                             1,259
      ProviderError / rca                                       133
      CircuitOpenError / rca                                     46
      ProviderError, AllProvidersUnavailableError / telegram      51

So 86% of these were failures the platform could name in a sentence, told to him
as a stack-trace fragment instead.

WHAT THIS CHANGE DOES NOT DO. It does not remove the technical detail — he is the
engineer on this system and "Provider 'NeraAiRaw' error" is genuinely useful. The
explanation LEADS and the detail follows, so nothing is lost and the first thing
he reads is what happened.

AND THE CATALOGUE GETS ONE OWNER. ``explain_failure_class`` moves to
``setup.localize``, beside the ``floor_cause_*`` strings it reads, because a
lookup that lives in one of its two consumers is how the other consumer came to
not have it. Same lesson as the event-name constants: the module that owns the
data owns the accessor.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.setup.localize import explain_failure_class

#: The four classes that account for 94% of what reaches him.
MEASURED_CLASSES = (
    "AllProvidersUnavailableError",
    "OwlTimeoutError",
    "CircuitOpenError",
    "ProviderError",
)

#: Verbatim from the 19:00:25 outcome row.
LIVE_ERROR = "Provider 'NeraAiRaw' error: Connection error."


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


def test_the_composed_floor_explains_a_class_it_can_name() -> None:
    """THE DEFECT. This is the exact call behind attempt 1 tonight."""
    out = synthesize_floor(
        goal=None, error=LIVE_ERROR, attempts=[], partial=None,
        failure_class="ProviderError",
    )
    expected = explain_failure_class("ProviderError", "en")
    assert expected and expected in out, (
        f"the floor still leads with a stack-trace fragment: {out!r}"
    )


def test_the_technical_detail_is_kept_not_replaced() -> None:
    """He is the engineer on this system. The explanation leads; the raw error
    still follows, so the change adds a sentence rather than hiding one."""
    out = synthesize_floor(
        goal=None, error=LIVE_ERROR, attempts=[], partial=None,
        failure_class="ProviderError",
    )
    assert LIVE_ERROR in out, out


@pytest.mark.parametrize("cls", MEASURED_CLASSES)
def test_both_renderers_agree_about_what_a_class_means(cls: str) -> None:
    """THE ROOT-CAUSE GUARD. Two floors that explain the same failure
    differently is the defect itself, not a cosmetic difference — tonight he got
    two answers to one outage within thirty seconds. Whatever
    ``_neutral_fallback`` would say, the composed floor must say too."""
    from stackowl.pipeline.delivery_gate import explain_failure_class as gate_lookup

    prose = gate_lookup(cls, "en")
    assert prose, f"{cls} has no catalogue entry"
    out = synthesize_floor(
        goal=None, error="boom", attempts=[], partial=None, failure_class=cls,
    )
    assert prose in out, f"{cls}: composed floor omits the shared explanation: {out!r}"


def test_the_catalogue_has_exactly_one_accessor() -> None:
    """The lookup used to live inside one of its two consumers, which is how the
    other consumer came to not have it. Both must resolve to the SAME function —
    an equal-looking copy would drift the first time one catalogue entry is
    reworded."""
    from stackowl.pipeline import delivery_gate

    assert delivery_gate.explain_failure_class is explain_failure_class


# --------------------------------------------------------------------------- #
# What must not go wrong                                                       #
# --------------------------------------------------------------------------- #


def test_an_unknown_class_is_never_given_an_invented_cause() -> None:
    """The generic rendering must survive for genuinely unknown failures.
    Manufacturing a cause is the overclaim this floor exists to prevent."""
    out = synthesize_floor(
        goal=None, error=LIVE_ERROR, attempts=[], partial=None,
        failure_class="SomeNeverSeenError",
    )
    assert LIVE_ERROR in out
    assert "floor_cause" not in out, f"a raw localization key reached the user: {out!r}"


def test_no_failure_class_behaves_exactly_as_before() -> None:
    """Most call sites cannot know the class. Passing nothing must be
    byte-identical to the previous behaviour, or this change would alter floors
    it was never meant to touch."""
    args = dict(goal=None, error=LIVE_ERROR, attempts=[], partial=None)
    assert synthesize_floor(**args) == synthesize_floor(**args, failure_class=None)  # type: ignore[arg-type]


def test_a_floor_with_no_error_is_unchanged_by_a_class() -> None:
    """The class explains an ERROR. A turn that failed with no error text has
    nothing to explain, and inventing a sentence there would contradict the
    'tried things, nothing reported a failure' branch that already exists."""
    out = synthesize_floor(
        goal="do the thing", error=None, attempts=["web_search"], partial=None,
        failure_class="ProviderError",
    )
    prose = explain_failure_class("ProviderError", "en")
    assert prose and prose not in out, out


@pytest.mark.parametrize("lang", ["en", "de", "fr", "es"])
def test_the_explanation_is_localized_in_the_composed_floor(lang: str) -> None:
    """``localize`` returns the KEY when a string is missing, so an untranslated
    entry would print a bare key at a user in that language."""
    out = synthesize_floor(
        goal=None, error=LIVE_ERROR, attempts=[], partial=None,
        failure_class="CircuitOpenError", lang=lang,
    )
    assert "floor_cause" not in out, out
    assert explain_failure_class("CircuitOpenError", lang) in out


def test_the_floor_still_never_raises_or_returns_empty() -> None:
    """The TerminalResponseGuarantee. A new parameter must not create a path
    where the floor is empty — that is the one thing this function may never do."""
    for cls in (None, "", "ProviderError", "Weird"):
        out = synthesize_floor(
            goal=None, error=None, attempts=None, partial=None, failure_class=cls,
        )
        assert out and out.strip()
