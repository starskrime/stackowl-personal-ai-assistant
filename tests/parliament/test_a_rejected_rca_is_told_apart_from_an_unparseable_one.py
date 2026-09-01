"""86 of 100 root-cause analyses conclude ``verified: False`` and nothing says why.

MEASURED 2026-08-31, the day the incident lane spent 15,724,829 input tokens —
72.3% of everything the platform spent::

    100 RCA completions   verified TRUE  12
                          verified FALSE 86
                          none            2
    350 `staged.analyze: exit` records, every one carrying all three stages
        ['rca_gatherer', 'hypothesis', 'verifier'] and a proposed skill_name

So the analyses are not failing: all three stages run, a fix is proposed, and then
the verdict is dropped. ``FailureOutcomeMiner`` authors a SKILL.md only for a
``verified=True`` verdict, so roughly 10.7M tokens a day buy proposals that are
discarded — and there is no way to know whether that is right.

BECAUSE TWO DIFFERENT THINGS LOOK IDENTICAL. ``_build_verdict`` computes::

    verdict_token = (_block_field(verdict_text, "VERDICT") or "").strip().upper()
    verified = verdict_token.startswith("VERIFIED")

and its own docstring admits the collision: "a REJECTED **or missing** verdict
yields verified=False". The token is then thrown away. "The verifier read the
evidence and said no" and "the verifier did not answer in the expected form" are
recorded the same way, and the second is exactly the failure mode a local model
produces — this same evening, 15 of 64 reflection failures were a valid object with
one required key simply absent.

THIS PROGRAMME HAS ALREADY PAID FOR THIS EXACT SHAPE. D09.1's own note, one module
over: the reflection failure record carried a 200-char preview and nothing else, and
"three separate reviewers read those previews as proof that ``suggested_strategy``
was missing and proposed relaxing the parser; on this field that was a guess,
because it cannot tell 'key absent' from 'key past the cap' from 'model output
truncated'."

SO THIS SHIPS THE INSTRUMENT AND NOT A FIX. It would be easy, and wrong, to decide
now that a missing VERDICT should count as verified — that is the guess D09.1 names.
What the token cannot currently answer, it will answer tomorrow.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.parliament.staged_rca import _build_verdict, verdict_shape


class _Evidence:
    """Only the fields `_build_verdict` reads — kept minimal on purpose, and
    completed against the real attribute list rather than guessed."""

    incident_id = "incident-abc"
    capability_class = "shell"
    failure_class = "stop"
    brief = "shell kept failing"
    parent_trace_ids: tuple[str, ...] = ()


_HYPOTHESIS = "ROOT_CAUSE: the tool was retried blindly\nFIX: verify the effect first"


def test_an_explicit_REJECTION_is_named() -> None:
    """The verifier read the evidence and said no. That is a real answer."""
    assert verdict_shape("VERDICT: REJECTED\nROOT_CAUSE: x\nFIX: y") == "rejected"


def test_a_VERIFIED_verdict_is_named() -> None:
    assert verdict_shape("VERDICT: VERIFIED\nROOT_CAUSE: x\nFIX: y") == "verified"


def test_a_MISSING_verdict_is_its_own_shape() -> None:
    """The whole point. This is what 86 unexplained rejections may actually be."""
    assert verdict_shape("ROOT_CAUSE: x\nFIX: y") == "absent"


def test_an_UNRECOGNISED_token_is_not_silently_a_rejection() -> None:
    """A model that answers "VERDICT: probably" has not rejected anything."""
    assert verdict_shape("VERDICT: probably\nROOT_CAUSE: x") == "unrecognised"


def test_the_shape_is_RECORDED_at_INFO(caplog: pytest.LogCaptureFixture) -> None:
    """Production runs at INFO, and this is the line that makes the 86 explicable.
    A DEBUG line here could never answer the question it exists for."""
    with caplog.at_level(logging.INFO):
        _build_verdict(_Evidence(), _HYPOTHESIS, "ROOT_CAUSE: a\nFIX: b")

    records = [r for r in caplog.records if "verdict" in r.getMessage().lower()]
    assert records, "the disposition is still unrecorded"
    fields = getattr(records[-1], "_fields", {})
    assert fields.get("verdict_shape") == "absent"


def test_the_BEHAVIOUR_is_unchanged() -> None:
    """The instrument must not become the fix. Deciding that a missing VERDICT
    counts as verified is exactly the guess D09.1 warns about — it waits for the
    evidence this line will collect."""
    verdict = _build_verdict(_Evidence(), _HYPOTHESIS, "ROOT_CAUSE: a\nFIX: b")
    assert verdict is not None
    assert verdict.verified is False

    rejected = _build_verdict(_Evidence(), _HYPOTHESIS, "VERDICT: REJECTED\nFIX: b")
    assert rejected is not None
    assert rejected.verified is False

    ok = _build_verdict(_Evidence(), _HYPOTHESIS, "VERDICT: VERIFIED\nROOT_CAUSE: a\nFIX: b")
    assert ok is not None
    assert ok.verified is True


def test_an_empty_verifier_response_is_absent_not_unrecognised() -> None:
    """A stage that returned nothing is a different fault from one that answered
    oddly, and the retry ladder already treats them differently."""
    assert verdict_shape("") == "absent"
    assert verdict_shape("   ") == "absent"
