"""An incident lesson must survive the gate that guards the skill corpus.

MEASURED across every retained log, 2026-08-29: `[incident] miner.author_one:
gated write refused — skipping` appears **48 times, and 48 of 48 (100%) cite the
same cause**::

    skill 'incident_shell_stop' does not meet the authoring standard (v1):
    description: 169 characters exceeds the 60-character limit — it is a one-line
    label, not the explanation. Put the detail in when_to_use, which is what
    retrieval reads.

So the self-healing loop opens an incident on a real signature, runs a staged RCA,
reaches a conclusion — and throws the lesson away at the final step. Every time.
Not one incident lesson has ever reached disk.

THE COST, measured in one 10-minute window after a restart: four incidents opened
(outcome:shell:stop, outcome:shell:unachieved_effect, outcome:web_fetch:stop,
outcome:delegate_task:unachieved_effect) consuming 340,830 of 358,951 background
input tokens — 95% of all background spend — for zero recorded lessons.

THE SHAPE: a producer that does not know its consumer's contract. `_author_one`
clamps description to 300 chars; `standard.MAX_DESCRIPTION_CHARS` is 60. The gate
is correct, and its message even states the remedy. The miner was simply never
taught the rule. The irony is one function away — `_render_incident_body` already
"iterates REQUIRED_SECTIONS rather than restating it, so a section added to the
standard appears here automatically". The body asks the standard; the description
did not.

NOTHING MAY BE LOST. The standard's own comment defines the mechanism: "the
retrieval signal that cap removes is recovered by making when_to_use a required
rich field: the embedder composes name + description + when_to_use + body, and
skills_fts indexes when_to_use too, so the ~137 stripped characters MOVE rather
than go." So the overflow moves into when_to_use; it is never truncated away.
"""

from __future__ import annotations

from stackowl.skills.standard import MAX_DESCRIPTION_CHARS


def _verdict(description: str, when_to_use: str = "Use after a shell failure."):  # noqa: ANN202
    from stackowl.learning.failure_outcome_miner import RcaVerdict

    return RcaVerdict(
        capability_class="shell",
        failure_class="stop",
        skill_name="incident_shell_stop",
        description=description,
        when_to_use=when_to_use,
        root_cause="the command never terminated",
        fix_pattern="bound it with a timeout",
        parent_trace_ids=["t1"],
    )


# The real refused text, from the live log line.
_REAL = (
    "When a shell command is stopped by the turn budget rather than completing, the "
    "recorded outcome is a stop rather than a failure, so the retry path never runs "
    "and the work silently does not happen."
)


def test_a_long_description_is_CUT_DOWN_to_the_standards_limit() -> None:
    """The defect: 169 chars offered against a 60-char limit, 48 times."""
    from stackowl.learning.failure_outcome_miner import _fit_to_standard

    assert len(_REAL) > MAX_DESCRIPTION_CHARS
    desc, _when = _fit_to_standard(_verdict(_REAL))
    assert len(desc) <= MAX_DESCRIPTION_CHARS, (
        f"description is still {len(desc)} chars — the gate refuses it, exactly as "
        f"it has 48 times: {desc!r}"
    )
    assert desc.strip(), "the description was emptied rather than shortened"


def test_the_EXPLANATION_is_not_lost_it_MOVES() -> None:
    """The standard's own stated mechanism: the stripped characters move, not go."""
    from stackowl.learning.failure_outcome_miner import _fit_to_standard

    _desc, when = _fit_to_standard(_verdict(_REAL))
    assert "retry path never runs" in when, (
        "the RCA's explanation was truncated away — the platform spent ~85k tokens "
        f"reaching it. when_to_use={when!r}"
    )


def test_the_ORIGINAL_when_to_use_also_survives() -> None:
    """Moving the overflow must not overwrite what was already there."""
    from stackowl.learning.failure_outcome_miner import _fit_to_standard

    _desc, when = _fit_to_standard(_verdict(_REAL, "Use after a shell failure."))
    assert "Use after a shell failure." in when


def test_a_SHORT_description_is_left_exactly_alone() -> None:
    """The guard must be narrow — a compliant verdict must pass through untouched."""
    from stackowl.learning.failure_outcome_miner import _fit_to_standard

    short = "Shell stops are not retried."
    desc, when = _fit_to_standard(_verdict(short, "Use after a shell failure."))
    assert desc == short
    assert when == "Use after a shell failure."


def test_the_label_still_says_WHAT_it_is_about() -> None:
    """A shortened label that identifies nothing is not worth writing."""
    from stackowl.learning.failure_outcome_miner import _fit_to_standard

    desc, _when = _fit_to_standard(_verdict(_REAL))
    low = desc.lower()
    assert "shell" in low or "stop" in low, (
        f"the label names neither the capability nor the failure: {desc!r}"
    )


def test_it_asks_the_STANDARD_rather_than_hardcoding_60() -> None:
    """Two copies of one rule is how the two silently disagree.

    This is precisely how the defect arose: `_author_one` carried its own 300, the
    standard carried 60, and nothing reconciled them.
    """
    import inspect

    from stackowl.learning import failure_outcome_miner as mod

    src = inspect.getsource(mod._fit_to_standard)
    assert "MAX_DESCRIPTION_CHARS" in src, "the limit is restated instead of asked for"
    # The DOCSTRING may say 60 — recording why the defect happened is the point of
    # it. The CODE may not, so drop the docstring (everything between the first
    # pair of triple quotes) before checking.
    parts = src.split('"""')
    body = parts[0] + "".join(parts[2:]) if len(parts) >= 3 else src
    assert "60" not in body, f"the 60 is hardcoded a second time:\n{body}"


def test_the_result_PASSES_the_real_gate() -> None:
    """The check that matters. Not "shorter" — ACCEPTED by the same validator.

    A test asserting only `len(desc) <= 60` would pass while some other clause of
    the standard still refused the write, which is the whole failure mode here:
    the producer satisfied its own idea of the rule and not the consumer's.
    """
    from stackowl.learning.failure_outcome_miner import _fit_to_standard
    from stackowl.skills.standard import validate_frontmatter

    desc, when = _fit_to_standard(_verdict(_REAL))
    problems = validate_frontmatter(
        {"name": "incident_shell_stop", "description": desc, "when_to_use": when}
    )
    assert not problems, f"the real validator still refuses it: {problems}"
