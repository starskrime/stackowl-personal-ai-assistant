"""Tests for off_topic delegation status + honest terminal result builders.

Tests that:
1. "off_topic" is a recognised DelegationStatus.
2. Each honest builder returns success=False with the FAILED token + do-not-retry
   guidance visible in the output or error field.
"""

from __future__ import annotations

import json

from stackowl.owls.a2a_delegation import _KNOWN_STATUSES
from stackowl.tools.agents.results import (
    honest_irrelevant_result,
    honest_offtopic_write_result,
    honest_uncertain_result,
)


def test_off_topic_is_a_known_status():
    assert "off_topic" in _KNOWN_STATUSES


def test_honest_builders_carry_failed_token_and_no_retry():
    for tr in (
        honest_uncertain_result("coder", 0.0),
        honest_offtopic_write_result("coder", 0.0),
        honest_irrelevant_result(0.0),
    ):
        blob = (tr.output or "") + (tr.error or "")
        assert "FAILED" in blob
        assert "retry" in blob.lower()
        assert tr.success is False
