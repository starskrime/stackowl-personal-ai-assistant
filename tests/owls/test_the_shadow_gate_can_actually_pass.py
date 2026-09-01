"""A promotion gate that cannot pass is not a gate, it is a wall.

MEASURED 2026-09-01: **40 shadow-gate rejections across six owls** — librarian,
archivist, mailbutler, secretary, scout, syshealth, five each — every one of them
carrying ``consecutive_non_regressions: 0`` against ``n_consecutive_required: 3``.
Zero. Always. So DNA promotion, the platform's own self-improvement gate, had
never once passed.

IT WAS NOT A STRICT THRESHOLD. It was a check the harness made impossible. The
secretary rejection says it plainly::

    pipeline_errors=["execute: ToolCallLeakError: Owl 'secretary' produced a
    tool call where a final answer was required — floored instead of leaking
    raw text"]

``ShadowValidator`` deliberately wires NO ``tool_registry`` into its scratch
services — that is the isolation seam and the module docstring defends it at
length, because a shadow replay must never be able to cause side effects. But
``_eligible_for_replay`` selected any *successful* past turn, and a successful
past turn is overwhelmingly a TOOL-USING one. Replaying it without tools makes
the owl reach for a tool that is not there; the pipeline raises
ToolCallLeakError; that lands in ``result_state.errors``; ``success`` is False;
the streak breaks at the first replay. Forever, for every tool-using owl.

THE FILTER'S OWN DOCSTRING ASKED THE RIGHT QUESTION and answered only half of
it: "did this input previously produce a clean, trustworthy result, so replaying
it is a meaningful regression check?" For a tool-using turn in a tool-free
harness the answer is NO, and nothing checked that half.

WHAT THE FIX LEAVES BEHIND, honestly: in the 14-day window this leaves
hypothesis 659, verifier 544, rca_gatherer 435 and secretary 56 replayable
samples against a required 5 — and drops mailbutler (4), jobmarket (3),
archivist (1) and headhunter (0) below the cold-start guard. That is the RIGHT
answer for an owl whose history is entirely tool-driven: "insufficient held-out
sample", fail-closed and legible, instead of a spurious regression against an
artefact of the harness.
"""

from __future__ import annotations

from types import SimpleNamespace

from stackowl.owls.shadow_validator import _eligible_for_replay


def _outcome(*, success: bool = True, failure_class=None, tools=()):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        success=success, failure_class=failure_class, tool_sequence=tools,
        input_text="q", trace_id="t",
    )


def test_a_tool_using_turn_is_not_replayable() -> None:
    """The defect. Selecting it guarantees a ToolCallLeakError in a harness that
    wires no tools, which the gate then counts as a regression."""
    assert _eligible_for_replay([_outcome(tools=("shell", "read_file"))]) == []


def test_a_tool_free_success_is_still_selected() -> None:
    """The expensive direction: over-filtering would starve every owl and turn a
    passable gate into a permanent cold start — the same wall by another route."""
    assert len(_eligible_for_replay([_outcome(tools=())])) == 1


def test_a_failure_is_still_excluded() -> None:
    """The original contract must survive: replaying a turn that failed is not a
    regression check, it is a re-run of a known-bad input."""
    assert _eligible_for_replay([_outcome(success=False)]) == []
    assert _eligible_for_replay([_outcome(failure_class="stop")]) == []


def test_order_is_preserved() -> None:
    """The caller takes ``eligible[:sample_size]`` and relies on newest-first."""
    rows = [_outcome(tools=()) for _ in range(3)]
    rows[1].input_text = "second"
    assert [o.input_text for o in _eligible_for_replay(rows)][1] == "second"


def test_a_missing_tool_sequence_is_treated_as_tool_free() -> None:
    """None rather than () — an older row must not crash the filter, and a turn
    with no recorded tools is replayable by the same reasoning."""
    assert len(_eligible_for_replay([_outcome(tools=None)])) == 1


def test_the_filter_states_why_replayability_matters() -> None:
    """Structural. A later reader dropping the tool_sequence clause to "widen the
    sample" would silently restore a gate that can never pass, so the measurement
    has to live where they will see it."""
    import inspect

    doc = inspect.getdoc(_eligible_for_replay) or ""
    assert "ToolCallLeakError" in doc and "tool_registry" in doc, (
        "the reason replayability is required is not stated on the filter"
    )
