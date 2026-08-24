"""A turn cut off by the DEFAULT budget backstop must say so.

REPORTED BY BAKIR 2026-08-24, from Telegram. He asked a clear question and got
back one line — "Found the provider API. Now let me find the request shape." —
and then nothing. 358,961 tokens in, 4,654 out, and no answer.

WHAT ACTUALLY HAPPENED, trace 0e568f1ad39a4da485aa5552ac279f3b:

    02:12:29  his 160-char question routed to secretary
    02:12:39  browser_navigate hcpdirectory.cigna.com -> BrowserSessionLimitError
    02:13:00  browser_navigate  again          -> TimeoutError, 30s
    02:13:37  browser_navigate  again          -> BrowserSessionLimitError
              web_fetch x3                     -> blocked by the SSRF egress guard
              two same-tool circuit-opens
    02:15:24  [budget] gate: cap reached — cap="steps", limit=20, actual=20
    02:15:27  delivered 1 chunk, total_len=58   <- the progress line, as the answer
    02:15:27  success=False, failure_class="stop", 177s elapsed

The platform KNEW the turn failed. He was told nothing.

WHY NOTHING CAUGHT IT. The honest give-up floor arms on CONSEQUENTIAL failures —
tools that change the world. Every tool that failed here is `severity='read'`
with `effect_class=None`: browser_navigate, web_fetch, web_search. A research
turn fails on READ failures, so `cons_failures` was 0 and the floor never armed.
A turn cut off by the budget cap had no honesty gate at all when it never tried
to change anything.

AND THE ASYMMETRY THAT MAKES IT A BUG RATHER THAN A GAP. Two other paths through
the same code are already honest: a user-requested stop appends "[stopped: you
asked me to stop...]", and an EXPLICIT operator cap appends "[stopped: budget cap
'steps' reached...]". Only the DEFAULT backstop is silent — and it is the one the
user cannot predict, because he never set it.

The original reason is sound and preserved: the default path must not leak a
developer-facing marker like `budget:stop:steps:limit=20`. Saying nothing is not
the only alternative to saying that.
"""

from __future__ import annotations

import re

SRC = "src/stackowl/pipeline/steps/execute.py"


def _source() -> str:
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def _backstop_branch() -> str:
    """The `_default_backstop` branch of the BudgetBreach handler."""
    src = _source()
    start = src.index("except BudgetBreach as exc:")
    end = src.index("# Explicit cap: deliver partial with a human-visible budget note.", start)
    return src[start:end]


def test_the_default_backstop_appends_a_note() -> None:
    """The defect: a partial was delivered with nothing said about the stop."""
    branch = _backstop_branch()
    assert "_backstop_note" in branch, (
        "a turn cut off by the default budget backstop must tell the user it was "
        "cut off — silence is what produced Bakir's 'and nothing came after'"
    )


def test_the_note_is_plain_english_not_a_developer_marker() -> None:
    """The original concern stays honoured: no `budget:stop:steps:limit=20` in
    user-facing content. Plain English is the alternative to silence, not the
    marker."""
    branch = _backstop_branch()
    note = re.search(r'_backstop_note\s*=\s*\((.*?)\)', branch, re.S)
    assert note, "expected a _backstop_note literal"
    text = note.group(1)
    for leak in ("budget:stop", "limit=", "actual=", "exc.cap", "exc.limit"):
        assert leak not in text, f"developer-facing detail leaked into the note: {leak}"


def test_the_note_tells_the_user_what_to_DO() -> None:
    """A notice that only reports a failure leaves the user stuck. The whole
    reason this matters is that he asked a question and got no path forward."""
    branch = _backstop_branch()
    note = re.search(r'_backstop_note\s*=\s*\((.*?)\)', branch, re.S)
    assert note
    text = note.group(1).lower()
    assert "continue" in text or "again" in text, (
        "the note must offer a next step, not merely announce a stop"
    )


def test_an_empty_partial_still_routes_to_the_floor() -> None:
    """Unchanged guarantee: with no partial at all the turn goes to
    synthesize_floor, which is the never-empty promise. The note must be added
    ALONGSIDE that path, not instead of it."""
    branch = _backstop_branch()
    assert "synthesize_floor" in branch


def test_the_explicit_cap_path_is_untouched() -> None:
    """It was already honest. This change is scoped to the silent path."""
    src = _source()
    assert "[stopped: budget cap '" in src


def test_the_user_requested_stop_note_is_untouched() -> None:
    src = _source()
    assert "[stopped: you asked me to stop" in src
