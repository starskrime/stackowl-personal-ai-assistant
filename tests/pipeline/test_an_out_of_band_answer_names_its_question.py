"""An answer that arrives after the reader has gone must say what it answers.

Bakir, 2026-09-09: "I did ask question and it was answered to old my question which
tells me todays context switching, compaction, memory handling, long term memory does
not work correctly."

MEASURED, and the mechanism is the durable loop rather than memory. A chat task whose
LEASE expired is re-driven by `task_loop_runner` and its answer pushed into the chat
afterwards: **221 re-drives across the retained logs**, and the tasks are old — 83
retried tasks average **17.1 hours** between creation and last attempt, maximum
**52.6 hours**. Trace `6e6cf497` was asked at 02:49:45, re-driven at 02:56:27 as a new
message arrived, and delivered at 02:58:24.

THE ONLY TIME CONCEPT IN THAT PACKAGE IS THE LEASE, and a lease answers "did the worker
die", not "is this still wanted". `task_loop_runner.py` says so itself: "A chat task
only reaches here after its LEASE EXPIRED." Nothing bounds relevance.

WHY THE LABEL RATHER THAN SUPPRESSION. `_proactive_fallback` fires precisely when the
live reader is gone — 43 times against 1,176 normal deliveries — so the recipient is by
construction NOT the person waiting on a stream, and an unlabelled answer reads as a
reply to whatever they asked most recently. Naming the question is additive and needs no
product decision. Whether a 52-hour-old answer should be SENT AT ALL is user-facing
removal, which this programme escalates rather than decides; it is queued as ESC-161.
"""

from __future__ import annotations

import inspect

import pytest

from stackowl.pipeline.steps import deliver


class _State:
    """Only what `_answering_lead` reads. A fuller double would drift from the real
    PipelineState without testing anything more."""

    def __init__(self, input_text: str, input_is_command: bool = False) -> None:
        self.input_text = input_text
        self.input_is_command = input_is_command


def test_the_answer_names_the_question_it_answers() -> None:
    lead = deliver._answering_lead(_State("what is the weather in Baku"))  # noqa: SLF001

    assert "what is the weather in Baku" in lead
    assert lead.endswith("\n\n"), "the lead must not run into the answer body"


def test_a_long_question_is_quoted_not_dumped() -> None:
    """The lead is a signpost, not a transcript — an unbounded quote would push the
    answer itself off the first screen of a phone."""
    lead = deliver._answering_lead(_State("x" * 500))  # noqa: SLF001

    assert len(lead) < 140, f"the lead grew to {len(lead)} chars"
    assert "…" in lead, "a truncated quote must show that it was truncated"


def test_a_command_authored_turn_is_not_quoted_back() -> None:
    """`input_text` for a command was written by the platform, not typed by the user.
    Quoting it back as "your earlier message" would be a small lie."""
    assert deliver._answering_lead(_State("/status", input_is_command=True)) == ""  # noqa: SLF001


def test_nothing_is_invented_when_there_is_nothing_to_quote() -> None:
    for empty in ("", "   "):
        assert deliver._answering_lead(_State(empty)) == ""  # noqa: SLF001


@pytest.mark.tripwire
def test_the_lead_is_actually_wired_into_the_out_of_band_send() -> None:
    """WIRED, NOT DECORATION. A lead function nothing calls would be this repo's
    most-recorded shape, and the whole point is that the USER sees it."""
    src = inspect.getsource(deliver._proactive_fallback)  # noqa: SLF001

    assert "_answering_lead(state)" in src, (
        "the out-of-band send no longer prefixes the answer with its question"
    )
    assert "message=body," not in src, "the unlabelled body is being sent again"


def test_the_normal_in_band_reply_is_left_alone() -> None:
    """The label belongs ONLY to the out-of-band path. Prefixing every answer would
    put "Answering your earlier message" on a live reply the user is watching arrive."""
    module_src = inspect.getsource(deliver)

    assert module_src.count("_answering_lead(") == 2, (
        "the lead is used somewhere other than its definition and the out-of-band "
        "send — a live in-band reply must not be prefixed"
    )
