"""The router asked for two lines, got them, and threw the answer away.

MEASURED across every retained log: 46 records of ``[router] _parse_intent_class:
no valid class token — fail-safe to standard``, and every single one carries the
same shape of reply::

    28  'Line 1: secretary\\nLine 2: standard'
     7  'Line 1: jobmarket\\nLine 2: standard'
     6  'Line 1: headhunter\\nLine 2: standard'
     3  'Line 1: syshealth\\nLine 2: standard'
     2  'Line 1: mailbutler\\nLine 2: standard'

The model answered correctly and echoed OUR OWN LABELS back with it — the prompt
says "Line 1 (...)", "Line 2 (...)", "Line 3 (ONLY if line 2 is 'clarify')".

REPRODUCED THROUGH THE REAL PARSERS, on those exact five strings::

    owl=secretary  class=standard
    owl=secretary  class=standard   <<< model said jobmarket
    owl=secretary  class=standard   <<< model said headhunter
    owl=secretary  class=standard   <<< model said syshealth
    owl=secretary  class=standard   <<< model said mailbutler

So EIGHTEEN of the 46 named a specialist and were routed to the secretary instead.

AND THE OWL HALF FAILED SILENTLY. The class miss logs at WARNING; the owl miss logs
at DEBUG — "[router] _parse_choice: no fuzzy match — secretary fallback" — and
production runs at INFO, so the misrouting left no record at all. The only reason it
is visible is that its noisy sibling was in the same reply.

THE FIX IS STRUCTURAL, NOT A VOCABULARY. It does not look for the word "Line": it
tries the whole line first, exactly as today, and only if that fails tries the text
after the last colon. So it can recover and cannot regress, it needs no English
keyword (a standing rule here), and it works for any label the model invents.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.owls.router import FuzzyMatcher, SecretaryRouter

_KNOWN = {"secretary", "jobmarket", "headhunter", "syshealth", "mailbutler", "scout"}


def _router() -> SecretaryRouter:
    r = SecretaryRouter.__new__(SecretaryRouter)
    r._fuzzy = FuzzyMatcher()  # noqa: SLF001
    return r


@pytest.mark.parametrize("owl", ["jobmarket", "headhunter", "syshealth", "mailbutler"])
def test_a_labelled_reply_routes_to_the_owl_the_model_CHOSE(owl: str) -> None:
    """The 18 misroutings, one per measured owl."""
    reply = f"Line 1: {owl}\nLine 2: standard"

    assert _router()._parse_choice(reply, _KNOWN) == owl


def test_a_labelled_reply_keeps_its_intent_class() -> None:
    reply = "Line 1: jobmarket\nLine 2: conversational"

    assert _router()._parse_intent_class(reply, "t") == "conversational"


def test_an_UNLABELLED_reply_is_unchanged() -> None:
    """The format the prompt actually asks for must keep working byte-identically."""
    reply = "jobmarket\nstandard"
    r = _router()

    assert r._parse_choice(reply, _KNOWN) == "jobmarket"
    assert r._parse_intent_class(reply, "t") == "standard"


def test_an_UNKNOWN_owl_still_falls_back() -> None:
    """The label strip must not become a way to route to something that does not
    exist — the fallback is what stops a hallucinated name reaching the pipeline."""
    assert _router()._parse_choice("Line 1: nosuchowl\nLine 2: standard", _KNOWN) == "secretary"


def test_the_FUZZY_correction_still_works_through_a_label() -> None:
    """PARL-6's near-miss correction is upstream of the fallback and must survive:
    a typo inside a label is still a typo."""
    assert _router()._parse_choice("Line 1: jobmarkett\nLine 2: standard", _KNOWN) == "jobmarket"


def test_an_EMPTY_reply_still_fails_safe_to_conversational() -> None:
    """Unchanged and load-bearing: no signal at all means no tool loop, which is a
    different decision from 'the reply was unparseable'."""
    assert _router()._parse_intent_class("", "t") == "conversational"


def test_a_reply_with_a_colon_in_the_NAME_is_not_broken() -> None:
    """Try the whole line FIRST. A name that legitimately contains a colon must not
    be cut by a rule that exists for labels."""
    known = {*_KNOWN, "ops:oncall"}
    assert _router()._parse_choice("ops:oncall\nstandard", known) == "ops:oncall"


def test_the_silent_owl_fallback_is_now_VISIBLE(caplog: pytest.LogCaptureFixture) -> None:
    """It logged at DEBUG while production runs at INFO, so 46 misroutings left no
    record. A fallback that decides which agent answers the user is not debug."""
    with caplog.at_level(logging.INFO):
        _router()._parse_choice("totally-unknown-name\nstandard", _KNOWN)

    records = [
        r for r in caplog.records
        if "fallback" in r.getMessage().lower() and r.levelno >= logging.INFO
    ]
    assert records, "the routing fallback is still invisible in production"
