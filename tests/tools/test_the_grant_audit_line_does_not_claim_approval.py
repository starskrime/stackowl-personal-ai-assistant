"""The grant audit line must not claim a human approved something they did not.

MEASURED 2026-08-26. A grant fired with no human present:

    05:58:18  owl_build.grant: authority WIDENED with the user's approval
              {"owl": "jobmarket", "granted": ["objective"]}

and the consent record for the SAME event in the SAME second read

    05:58:18  [consent] policy.request: exit  reason="official_channel"
    05:58:18  [consent] authority judged by ORIGIN, not by attendance

The user had approved nothing. The request had merely ARRIVED on an official
channel, which this platform deliberately treats as carrying the owner's
authority whether or not they are watching (consent.py's _PROVENANCE_CATEGORIES,
whose own comment argues the rule is strictly safer than what preceded it).

THE RULE IS RIGHT AND IS NOT UNDER TEST HERE. The SENTENCE was wrong, and this is
the line someone reads when asking "why does this owl hold that tool?" — so
"the user approved it" ends the enquiry at exactly the wrong place, and the real
answer, which lives in the consent decision for that trace, never gets looked up.

Fifth instance in one night of a name or message asserting something other than
what happened, after the scheduler timeout log ("freed for retry/re-arm" when
nothing was freed), two test-double docstrings vouching for the wrong parameter
name, and BrowserSessionLimitError being raised for a browser crash. That last
one cost a whole measurement round: I believed it and went hunting a session leak
that does not exist.
"""

from __future__ import annotations

import inspect


def test_the_emitter_does_not_assert_user_approval() -> None:
    """Asserted against the SOURCE, because the thing under test is a sentence.

    A behavioural test would need a live consent gate and a real grant, and would
    still not check the wording — which is the entire defect.
    """
    from stackowl.tools.meta import owl_build

    src = inspect.getsource(owl_build)
    # Strip docstrings/comments would be fragile; instead pin the log CALL itself.
    grant_calls = [
        line for line in src.splitlines()
        if "owl_build.grant: authority WIDENED" in line
    ]
    assert grant_calls, "the grant audit line vanished — it is the evidence line"
    for line in grant_calls:
        assert "with the user's approval" not in line, (
            "the grant audit line claims a human approved this. Authority here is "
            "judged by ORIGIN, not attendance, so that sentence is false whenever "
            "the request simply arrived on an official channel: " + line.strip()
        )


def test_the_emitter_points_at_where_the_basis_actually_lives() -> None:
    """Removing the false claim is not enough — a reader still needs the answer."""
    from stackowl.tools.meta import owl_build

    src = inspect.getsource(owl_build)
    assert "[consent] decision" in src, (
        "the audit line should point at the consent decision, which is what "
        "actually records the basis for widening authority"
    )


def test_the_origin_rule_itself_is_untouched() -> None:
    """Guards the thing this change deliberately did NOT do.

    If a later edit 'fixes' the honesty complaint by making authority require
    attendance, it would silently break unattended operation — which is the whole
    point of the 2026-08-19 authority-vs-action work.
    """
    from stackowl.tools import consent

    src = inspect.getsource(consent)
    assert "_PROVENANCE_CATEGORIES" in src
    assert "authority judged by ORIGIN, not by attendance" in src, (
        "the origin-based rule is deliberate; only the audit WORDING was wrong"
    )
    assert 'reason="official_channel"' in src
