"""Underscore emphasis is not intraword, and forgetting that ate every identifier.

WHAT HE RECEIVED, 2026-09-03:

    "shell/unachievedeffect has failed 5 more time(s) since a fix was written for
     it. The lesson 'incidentshellunachievedeffect' is owned by NOBODY"

Every underscore gone. The alert was correct when it was composed; the delivery
seam removed them. MEASURED in the live log —
``[notifications] deliverer: output style applied to a proactive message
{before_len: 262, after_len: 258}`` — exactly four characters for exactly four
underscores.

THE CAUSE. ``_EMPH_ITALIC_UNDER_RE`` was ``_(.+?)_`` with no flanking rule, so in
``shell_unachieved_effect`` it matched ``_unachieved_`` and stripped both
delimiters. It also spanned words: "web_fetch and browser_navigate" arrived as
"webfetch and browsernavigate" — two different capability names welded together.

WHAT ELSE THE SAME CAUSE REACHED. Every proactive message carrying a snake_case
name: capability and tool names, owl names like ``rca_gatherer``, skill names,
table names, file paths. Precisely the values an operator needs in order to act,
and it failed SILENTLY — the sentence still reads like prose, so nothing looked
wrong until someone tried to use a name that no longer existed.

THE FIX IS THE RULE MARKDOWN ALREADY HAS. CommonMark disallows intraword
underscore emphasis; the regex simply did not implement it. ``*`` is deliberately
untouched — CommonMark DOES allow intraword ``*``, and asterisks do not appear
inside identifiers, which is exactly why only underscores broke.
"""

from __future__ import annotations

import pytest

from stackowl.channels._format import OutputStyle, _strip_emphasis

#: The identifiers this platform actually sends, taken from the live alert and
#: from the capability vocabulary around it.
_IDENTIFIERS = (
    "shell_unachieved_effect",
    "incident_shell_unachieved_effect",
    "incident_browser_navigate_stop",
    "rca_gatherer",
    "web_fetch",
    "browser_navigate",
    "task_outcomes",
    "skill_ownership",
    "my_file.py",
)


@pytest.mark.parametrize("ident", _IDENTIFIERS)
def test_an_identifier_survives_emphasis_stripping(ident: str) -> None:
    assert _strip_emphasis(ident) == ident


def test_two_identifiers_in_one_sentence_are_not_WELDED_together() -> None:
    """The worst form of the bug: the match spanned from one name into the next,
    so two capabilities arrived as one word that names nothing."""
    text = "web_fetch and browser_navigate both failed"
    assert _strip_emphasis(text) == text


def test_real_emphasis_is_STILL_stripped() -> None:
    """The control. A guard that keeps identifiers by giving up on emphasis has
    simply disabled the feature the operator asked for — he complained about
    asterisks arriving in his replies, and that must stay fixed."""
    assert _strip_emphasis("**bold** and _italic_ and __also bold__") == (
        "bold and italic and also bold"
    )
    assert _strip_emphasis("a _phrase of words_ here") == "a phrase of words here"


def test_the_operators_actual_message_survives_the_delivery_seam() -> None:
    """End to end through the same entry point the deliverer calls."""
    style = OutputStyle(markdown="off")
    raw = (
        "A lesson is not holding — shell/unachieved_effect has failed 5 more "
        "time(s) since a fix was written for it. The lesson "
        "'incident_shell_unachieved_effect' is owned by NOBODY. "
        "Affected: mailbutler, rca_gatherer."
    )

    out = style.enforce(raw)

    assert "shell/unachieved_effect" in out
    assert "incident_shell_unachieved_effect" in out
    assert "rca_gatherer" in out


def test_stripping_stays_IDEMPOTENT() -> None:
    """``OutputStyle.verify`` re-runs every enforcer and expects a fixed point; a
    transform that keeps changing the text logs 'spec drift repaired' for ever."""
    text = "a _phrase_ with web_fetch and **bold** in it"
    once = _strip_emphasis(text)
    assert _strip_emphasis(once) == once
