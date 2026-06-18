"""Tests for the deterministic explicit-signal parser (concurrent-msg §6.1, Task 14).

The TurnRouter parser maps an EXPLICIT user signal at message-arrival (while a
turn is in-flight) to a routing decision WITHOUT any LLM call:

  * a recognised slash-command (``/stop`` / ``/steer`` / ``/new``) — language-neutral
    command tokens (reused via the gateway scanner's command extractor),
  * a Telegram reply-to-the-in-flight-message — a language-neutral STRUCTURAL signal,
  * a pending-clarify answer — folds into the existing clarify ANSWER path (REPLY),
  * anything else → NONE (UNSIGNALED → Task 15's conservative classifier decides).

No free-text English keyword is the matcher. The bare ``stop`` token is a small
CONFIGURABLE casefolded set (default includes common forms so the plan's example
passes) — never a hardcoded English literal baked into the control flow.
"""

from __future__ import annotations

import pytest

from stackowl.gateway.turn_router import (
    ExplicitSignal,
    StopTokens,
    parse_explicit_signal,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/stop", ExplicitSignal.STOP),
        ("stop", ExplicitSignal.STOP),
        ("/steer also include Y", ExplicitSignal.STEER),
        ("/new what's the weather", ExplicitSignal.NEW),
        ("just a normal message", ExplicitSignal.NONE),
    ],
)
def test_explicit_signal_parsing(text: str, expected: ExplicitSignal) -> None:
    assert parse_explicit_signal(text, is_reply_to_inflight=False) == expected


def test_telegram_reply_to_inflight_is_steer() -> None:
    assert (
        parse_explicit_signal("a correction", is_reply_to_inflight=True)
        == ExplicitSignal.STEER
    )


def test_slash_steer_wins_over_reply_structural() -> None:
    # An explicit /steer command and a reply both map to STEER (consistent).
    assert (
        parse_explicit_signal("/steer add Z", is_reply_to_inflight=True)
        == ExplicitSignal.STEER
    )


def test_slash_new_is_not_overridden_by_reply() -> None:
    # An explicit /new command is honoured even on a structural reply: /new is an
    # unambiguous user intent to start a fresh turn, not steer the running one.
    assert (
        parse_explicit_signal("/new something else", is_reply_to_inflight=True)
        == ExplicitSignal.NEW
    )


def test_leading_whitespace_and_case_insensitive_slash() -> None:
    assert parse_explicit_signal("   /STOP", is_reply_to_inflight=False) == ExplicitSignal.STOP
    assert parse_explicit_signal("  /Steer x", is_reply_to_inflight=False) == ExplicitSignal.STEER


def test_unknown_slash_command_is_not_a_steer_signal() -> None:
    # An unrelated slash-command (e.g. /help) is NOT an explicit steer/stop/new
    # signal — it is NONE here (it routes through the normal command path).
    assert parse_explicit_signal("/help me", is_reply_to_inflight=False) == ExplicitSignal.NONE


def test_bare_stop_token_set_is_configurable_not_hardcoded() -> None:
    # The bare stop token is a CONFIGURABLE casefolded set (no hardcoded English
    # literal in the control flow). A caller can supply a different/empty set.
    empty = StopTokens(frozenset())
    assert parse_explicit_signal("stop", is_reply_to_inflight=False, stop_tokens=empty) == ExplicitSignal.NONE
    # A custom multilingual token set works.
    custom = StopTokens(frozenset({"detener", "arrêter"}))
    assert (
        parse_explicit_signal("Detener", is_reply_to_inflight=False, stop_tokens=custom)
        == ExplicitSignal.STOP
    )


def test_slash_stop_always_works_regardless_of_token_set() -> None:
    # The language-neutral slash-command /stop is honoured even with an empty
    # bare-token set — the slash-command is the canonical signal.
    empty = StopTokens(frozenset())
    assert parse_explicit_signal("/stop", is_reply_to_inflight=False, stop_tokens=empty) == ExplicitSignal.STOP


def test_empty_message_is_none() -> None:
    assert parse_explicit_signal("", is_reply_to_inflight=False) == ExplicitSignal.NONE
    assert parse_explicit_signal("   ", is_reply_to_inflight=False) == ExplicitSignal.NONE


def test_never_raises_on_weird_input() -> None:
    # Fail-safe: any unrecognised / odd input → NONE, never a crash.
    for bad in ["///", "/", "/123", "‏/steer rtl", "🦉 owl"]:
        assert isinstance(
            parse_explicit_signal(bad, is_reply_to_inflight=False), ExplicitSignal
        )


def test_reply_to_inflight_with_empty_text_still_steer() -> None:
    # A structural reply with empty body is still a STEER signal (structural wins).
    assert parse_explicit_signal("", is_reply_to_inflight=True) == ExplicitSignal.STEER
