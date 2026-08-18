"""A repeatedly-failing tool must keep telling the model WHY.

BAKIR, 2026-08-18: "why does the tool not deliver failure to the model so the model
can rethink?"

MEASURED, and he was right about the effect if not the mechanism. The failure IS
delivered — but only the first time. execute.py collapsed every subsequent
deterministic failure of the same tool to:

    'shell' failed (non-retryable), 2 attempts this turn.
    Do not retry it — try a different approach or answer without it.

The error text is gone from attempt two onward, while "answer without it" stays.
So the model knows THAT shell failed and no longer knows WHY, and is explicitly
invited to proceed. That is what happened on his Gmail turn: shell failed twice,
the reason was withdrawn, and the model filled the gap with two fabricated
citations — which the grounding gate then had to block.

THE ORIGINAL REASON FOR COLLAPSING IS REAL and is not being discarded here:
re-appending a tool's FULL failure body every iteration drove input tokens 8k→11k
in a single production turn. The fix keeps the collapse and puts the REASON back
inside it, truncated — a bounded first line is what the model needs to change
approach, and it cannot grow the way an unbounded body did.
"""

from __future__ import annotations

from stackowl.pipeline.steps.execute import _collapsed_failure_text


class TestTheModelKeepsTheReason:
    def test_a_repeat_failure_still_carries_the_error(self) -> None:
        """The whole point. Without this the model is told only that something
        failed, which it cannot reason about."""
        out = _collapsed_failure_text(
            "shell", 2, "bash: gcloud: command not found",
        )

        assert "gcloud: command not found" in out

    def test_it_still_says_not_to_retry(self) -> None:
        """The guidance that made the collapse worth having is kept — repeating a
        deterministic failure is exactly what the loop guard exists to stop."""
        out = _collapsed_failure_text("shell", 3, "boom")

        assert "retry" in out.lower()
        assert "3" in out

    def test_a_long_error_is_TRUNCATED_not_dropped(self) -> None:
        """The bloat that justified collapsing came from re-appending the FULL
        body every iteration. A bounded prefix keeps the reason without the growth
        — dropping it entirely was an over-correction."""
        out = _collapsed_failure_text("shell", 2, "E" * 5000)

        assert "EEE" in out, "the reason was dropped rather than truncated"
        assert len(out) < 700, f"the collapse stopped bounding context: {len(out)}"

    def test_it_degrades_gracefully_with_no_error_text(self) -> None:
        """Some failures carry no message. The summary must still be useful rather
        than saying the tool failed 'because '."""
        out = _collapsed_failure_text("shell", 2, "")

        assert "shell" in out
        assert out.strip().endswith(".") or "approach" in out

    def test_the_tool_name_is_always_present(self) -> None:
        """With several tools failing in one turn, a summary that does not name
        which one is unactionable."""
        assert "browser_navigate" in _collapsed_failure_text(
            "browser_navigate", 2, "timeout",
        )
