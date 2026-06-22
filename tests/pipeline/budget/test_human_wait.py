"""Tests for the turn-scoped human-wait accumulator carrier."""

from __future__ import annotations

import math

from stackowl.pipeline.budget import human_wait


def test_record_and_read_within_bound_context() -> None:
    token = human_wait.bind()
    try:
        assert human_wait.current_human_wait_seconds() == 0.0
        human_wait.record_human_wait(3.0)
        human_wait.record_human_wait(2.5)
        assert human_wait.current_human_wait_seconds() == 5.5
    finally:
        human_wait.reset(token)


def test_non_positive_and_nan_are_ignored() -> None:
    token = human_wait.bind()
    try:
        human_wait.record_human_wait(0.0)
        human_wait.record_human_wait(-4.0)
        human_wait.record_human_wait(math.nan)
        assert human_wait.current_human_wait_seconds() == 0.0
        human_wait.record_human_wait(1.0)
        assert human_wait.current_human_wait_seconds() == 1.0
    finally:
        human_wait.reset(token)


def test_reset_restores_prior_context() -> None:
    token = human_wait.bind()
    human_wait.record_human_wait(7.0)
    assert human_wait.current_human_wait_seconds() == 7.0
    human_wait.reset(token)
    # After reset, back to the default (0.0) — no leak.
    assert human_wait.current_human_wait_seconds() == 0.0


def test_unbound_context_reads_zero_default() -> None:
    # No bind() in this context: reads the 0.0 default and record is harmless.
    assert human_wait.current_human_wait_seconds() == 0.0
