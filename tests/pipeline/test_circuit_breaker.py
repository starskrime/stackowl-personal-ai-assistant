"""Unit tests for the same-tool repeated-failure circuit breaker (incident P2)."""

from __future__ import annotations

from stackowl.pipeline.steps.execute import (
    SAME_TOOL_FAILURE_THRESHOLD,
    _circuit_open_refusal,
)


def test_threshold_is_three() -> None:
    # Host-agnostic fixed N; one below LoopGuard's identical-args break_at=4.
    assert SAME_TOOL_FAILURE_THRESHOLD == 3


def test_circuit_open_refusal_mentions_tool_and_steers_to_stop() -> None:
    msg = _circuit_open_refusal("shell")
    assert "shell" in msg
    # Steers the model to change approach or stop — no case-specifics.
    lower = msg.lower()
    assert "different" in lower or "another" in lower or "stop" in lower


def test_circuit_open_refusal_is_not_a_tool_failure_marker() -> None:
    # A bounce is containment, not a tool failure: it must NOT carry the marker
    # the give-up judge counts as a failed action (mirrors denied_this_run).
    from stackowl.pipeline.steps.execute import TOOL_FAILED_MARKER

    assert TOOL_FAILED_MARKER not in _circuit_open_refusal("shell")
