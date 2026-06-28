"""Unit tests for the PA3 turn-scoped escalation signal (breaker → ladder bridge)."""
from __future__ import annotations

import contextvars

from stackowl.providers.escalation_signal import (
    clear_escalation,
    escalation_requested,
    request_escalation,
)


def test_default_false_in_fresh_context() -> None:
    # A brand-new context never touched the var → default False, no contamination.
    ctx = contextvars.Context()
    assert ctx.run(escalation_requested) is False


def test_request_sets_true() -> None:
    clear_escalation()
    request_escalation("shell")
    try:
        assert escalation_requested() is True
    finally:
        clear_escalation()


def test_clear_resets_to_false() -> None:
    request_escalation("web_fetch")
    clear_escalation()
    assert escalation_requested() is False
