"""A backticked ordinary word must not refuse a skill — and advisories must be heard.

MEASURED across every retained log, 2026-08-29. The `tool_names` rule has refused
52 writes, blocking 13 distinct skills, and EVERY LIVE FIRING IS A FALSE POSITIVE.
Checked each flagged token against the live registry:

    registered tools among them : note_applied_lesson, web_search, owls_list
    NOT tools (18 of 21)        : stop, on_demand, english_tutor, scheduled, user,
                                  lifecycle, name, budget_exhausted, replace,
                                  search, delegate, mailbox, unachieved_effect,
                                  schedule, extract, snapshot, vision, wait_for

The three REAL tools were flagged in a single five-minute window on 2026-08-23
(08:31-08:36) and are pre-fix history — `_known_tool_names` used to return an empty
frozenset, a bug that file already documents and fixed. Everything since is prose:
failure classes (`stop`, `unachieved_effect`, `budget_exhausted`), lifecycle values
(`on_demand`, `scheduled`), an OWL name (`english_tutor`), and plain English
(`user`, `name`, `search`). The most recent firing was 2026-08-29T23:53:21 on
`stop`.

WHY THE RULE CANNOT BE SAVED BY A NARROWER REGEX. It matches on SHAPE
(``[a-z][a-z0-9_]{2,}`` in backticks) and tool names have no shape that domain
vocabulary lacks — `stop` and `search` are indistinguishable from real tool names
by any pattern. The comment already claims it is "deliberately narrow ... prose in
backticks must not be mistaken for a tool reference"; it is not narrow enough and
cannot be made so. An allowlist of excluded words is also out: this codebase
forbids hardcoded keyword lists.

SO IT BECOMES ADVISORY, not deleted. The rule's signal is real — a skill that names
a capability wrongly teaches the wrong name — it is simply not worth REFUSING a
skill over at a 100% live false-positive rate, especially now the learned corpus is
empty by choice and authoring is how it refills. Exactly the argument `Violation`
already makes for the soft line cap: "blocking distinguishes a rejection from a
warning ... because 'too detailed' is not the validator's judgement to make."

AND THE ADVISORY TIER HAD TO BE MADE REAL FIRST. `_standard_violations` returns
`standard.blocking(found)`, which DISCARDS every non-blocking violation with
nothing logging it. So `blocking=False` meant silently dropped — the soft line cap
has been decoration since it was written. Downgrading into that tier without fixing
it would have deleted the rule while appearing to keep it.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.skills import standard

_BODY = """## When to Use

When a shell command ends with a `stop` outcome rather than completing.

## Prerequisites

The task must already be `scheduled` and owned by a `user`.

## Procedure

1. Look at the `lifecycle` of the row.
2. Ask `english_tutor` for the analysis.

## Verification

The outcome is no longer `unachieved_effect`.

## Failure Modes

The budget reads `budget_exhausted`.
"""


def _tool_name_violations(body: str, known: frozenset[str]) -> list[standard.Violation]:
    return [v for v in standard.validate_body(body, known_tools=known) if v.rule == "tool_names"]


def test_backticked_prose_does_not_BLOCK_a_skill() -> None:
    """The defect: 52 refusals, 13 skills blocked, on words like `stop`."""
    found = _tool_name_violations(_BODY, frozenset({"web_search", "shell"}))
    blocking = [v for v in found if v.blocking]
    assert not blocking, (
        "backticked prose still refuses the write — these tokens are failure "
        f"classes, lifecycle values and an owl name, not capabilities: {blocking}"
    )


def test_the_signal_is_KEPT_as_an_advisory() -> None:
    """Downgraded, not deleted. A wrongly-named capability is still worth saying."""
    found = _tool_name_violations(_BODY, frozenset({"web_search", "shell"}))
    assert found, "the rule was removed rather than made advisory"
    assert all(v.blocking is False for v in found)


def test_an_advisory_violation_is_actually_LOGGED(caplog: pytest.LogCaptureFixture) -> None:
    """The tier had to be made real: `blocking()` discards these and nothing logged.

    Driven through the SOFT LINE CAP rather than tool_names, deliberately: in a bare
    test process `_known_tool_names()` returns None (no live registry), so the
    tool-name rule is skipped and produces no advisory at all. The line cap needs no
    registry, so it tests the advisory MECHANISM rather than one rule's inputs —
    which is the thing that was broken for every non-blocking rule.
    """
    from pathlib import Path

    from stackowl.skills.authoring import SkillWriteRequest, _standard_violations
    from stackowl.skills.manifest import SkillManifest

    request = SkillWriteRequest(
        target_dir=Path("/tmp/does-not-exist-skill"),
        manifest=SkillManifest(
            name="probe-skill", description="A short label.",
            when_to_use="Use when probing.", version="0.1.0", source="learned",
        ),
        body=_BODY + ("\nfiller line for the soft cap\n" * 250),
        skill_md_text="", consent_summary="",
        tool_name="skill_manage", channel="cli", session_key="s",
    )
    with caplog.at_level(logging.INFO):
        blocking = _standard_violations(request)

    assert not [v for v in blocking if v.rule == "tool_names"]
    assert not [v for v in blocking if v.rule == "length"], (
        "the soft line cap became blocking"
    )
    assert any("advisory violation" in r.getMessage().lower() for r in caplog.records), (
        "an advisory violation was discarded with nothing logged — the author is "
        "never told, which is deletion wearing the word 'warning'"
    )


def test_a_REAL_blocking_rule_still_refuses() -> None:
    """The guard must be narrow — the standard must still have teeth."""
    problems = standard.validate_frontmatter(
        {"name": "x", "description": "y" * 300, "when_to_use": "w"}
    )
    assert [v for v in problems if v.blocking], "the standard stopped refusing anything"
