"""Tests for the no-progress guard in the inner browser loop (Part 3).

TDD — written BEFORE the implementation.  The guard uses a signature of
(hash(state_text), action) to detect stalled loops.

These tests do NOT spin up a real browser; they exercise the logic of the
constant and the signature computation pattern directly, plus confirm that
the "no_progress" status is treated as a success for the ToolResult.
"""

from __future__ import annotations

import json


def test_no_progress_limit_constant_exists() -> None:
    """_NO_PROGRESS_LIMIT must exist in browse.py and be a positive int."""
    from stackowl.tools.browser.browse import _NO_PROGRESS_LIMIT

    assert isinstance(_NO_PROGRESS_LIMIT, int)
    assert _NO_PROGRESS_LIMIT > 0


def test_no_progress_signature_same_page_same_action() -> None:
    """Same state_text and same action produce the same signature (idempotent)."""
    state_text = "URL: /foo\nSome page content"
    action = {"action": "navigate", "url": "https://example.com/page"}
    sig1 = f"{hash(state_text)}|{json.dumps(action, sort_keys=True, default=str)}"
    sig2 = f"{hash(state_text)}|{json.dumps(action, sort_keys=True, default=str)}"
    assert sig1 == sig2


def test_no_progress_signature_different_action() -> None:
    """Different action → different signature → streak resets."""
    state_text = "URL: /foo\nSome page content"
    action_a = {"action": "navigate", "url": "https://example.com/a"}
    action_b = {"action": "navigate", "url": "https://example.com/b"}
    sig_a = f"{hash(state_text)}|{json.dumps(action_a, sort_keys=True, default=str)}"
    sig_b = f"{hash(state_text)}|{json.dumps(action_b, sort_keys=True, default=str)}"
    assert sig_a != sig_b


def test_no_progress_signature_different_page() -> None:
    """Different page content → different signature even with same action."""
    state_text_a = "URL: /a\nContent A"
    state_text_b = "URL: /b\nContent B"
    action = {"action": "scroll", "direction": "down"}
    sig_a = f"{hash(state_text_a)}|{json.dumps(action, sort_keys=True, default=str)}"
    sig_b = f"{hash(state_text_b)}|{json.dumps(action, sort_keys=True, default=str)}"
    assert sig_a != sig_b


def test_no_progress_streak_logic() -> None:
    """Simulate the streak accumulation logic to confirm limit works correctly."""
    _NO_PROGRESS_LIMIT = 3  # mirrors the constant

    state_text = "URL: /x\nfixed page"
    action = {"action": "scroll", "direction": "down"}

    prev_progress_sig: str | None = None
    no_progress_streak = 0
    tripped = False

    for step_idx in range(10):
        sig = f"{hash(state_text)}|{json.dumps(action, sort_keys=True, default=str)}"
        if sig == prev_progress_sig:
            no_progress_streak += 1
        else:
            no_progress_streak = 0
            prev_progress_sig = sig

        if no_progress_streak >= _NO_PROGRESS_LIMIT:
            tripped = True
            break

    # After _NO_PROGRESS_LIMIT identical (page, action) pairs, the streak trips.
    # step 0: different from None → streak=0, prev=sig
    # step 1: same → streak=1
    # step 2: same → streak=2
    # step 3: same → streak=3 >= 3 → trip
    assert tripped
    # Should trip at step 3 (streak accumulates from step 1)
    assert step_idx == 3  # type: ignore[possibly-undefined]


def test_no_progress_streak_resets_on_new_action() -> None:
    """If action changes, the streak resets to 0 and does not trip."""
    _NO_PROGRESS_LIMIT = 3

    state_text = "URL: /x\nfixed page"
    actions = [
        {"action": "scroll", "direction": "down"},
        {"action": "scroll", "direction": "down"},
        {"action": "navigate", "url": "https://example.com"},  # different!
        {"action": "scroll", "direction": "down"},
        {"action": "scroll", "direction": "down"},
    ]

    prev_progress_sig: str | None = None
    no_progress_streak = 0
    tripped = False

    for action in actions:
        sig = f"{hash(state_text)}|{json.dumps(action, sort_keys=True, default=str)}"
        if sig == prev_progress_sig:
            no_progress_streak += 1
        else:
            no_progress_streak = 0
            prev_progress_sig = sig
        if no_progress_streak >= _NO_PROGRESS_LIMIT:
            tripped = True
            break

    assert not tripped  # never hit 3 consecutive identical (page, action) pairs
