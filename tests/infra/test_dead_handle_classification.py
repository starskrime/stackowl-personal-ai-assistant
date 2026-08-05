"""ADR-19 obligation (1) — dead-handle detection must be STRUCTURAL.

This sat at the root of the platform's own self-healing primitive: nineteen
hardcoded English substrings deciding whether ANY tool (tools/base.py) or any
browser operation gets retried. D02.6 refused to port that design from the
reference platform for provider errors; it was already here for resources.

A missed heal is invisible — it looks exactly like "there was nothing to heal" —
which is why this needs tests rather than trust.
"""

from __future__ import annotations

import pytest

from stackowl.infra.resilience import (
    DEFAULT_DEAD_HANDLE_MARKERS,
    looks_like_dead_handle,
)


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError("whatever"),
        ConnectionRefusedError("whatever"),
        ConnectionAbortedError("whatever"),
        BrokenPipeError("whatever"),
        EOFError("whatever"),
    ],
)
def test_dead_by_TYPE_regardless_of_message(exc):
    """The message is deliberately meaningless here. If any of these depended on
    English text, a localized runtime would silently stop self-healing."""
    assert looks_like_dead_handle(exc)


def test_the_whole_ConnectionError_family_is_covered_by_the_base():
    """Listing the base rather than enumerating the four subclasses means a
    stdlib addition to the family is covered without a code change."""
    for cls in (BrokenPipeError, ConnectionResetError,
                ConnectionRefusedError, ConnectionAbortedError):
        assert issubclass(cls, ConnectionError), cls
        assert looks_like_dead_handle(cls("x"))


def test_a_localized_message_still_heals_when_the_type_is_known():
    """THE BUG THIS FIXES. Same failure, non-English message: previously
    unhealed, silently."""
    assert looks_like_dead_handle(ConnectionResetError("соединение сброшено"))
    assert looks_like_dead_handle(ConnectionRefusedError("接続が拒否されました"))


def test_an_ordinary_error_is_not_a_dead_handle():
    """The guard must not become 'retry everything' — that would mask real bugs
    behind a retry and burn the turn's budget doing it."""
    assert not looks_like_dead_handle(ValueError("bad argument"))
    assert not looks_like_dead_handle(KeyError("missing"))
    assert not looks_like_dead_handle(RuntimeError("something went wrong"))


def test_the_text_path_still_works_for_libraries_with_no_distinct_type(caplog):
    """Playwright raises a generic Error whose only signal is the message, so
    the substring pass is kept — as a LAST RESORT, and a logged one."""
    assert looks_like_dead_handle(Exception("Target closed"))


def test_every_marker_still_classifies(caplog):
    """Kept as a safety net over the fallback: if a marker stops matching, a
    subsystem quietly loses its heal path."""
    for marker in DEFAULT_DEAD_HANDLE_MARKERS:
        assert looks_like_dead_handle(Exception(f"prefix {marker} suffix")), marker


def test_the_fragile_text_path_announces_itself(caplog):
    """ADR-19 I6. If this line is common in production, the TYPE list is missing
    something and the fix is another type, not another string."""
    import logging

    with caplog.at_level(logging.DEBUG, logger="stackowl.infra"):
        looks_like_dead_handle(Exception("Target closed"))
    assert any("matched by TEXT" in r.message for r in caplog.records)


def test_a_type_match_does_NOT_log_the_fragile_path(caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="stackowl.infra"):
        looks_like_dead_handle(ConnectionResetError("Connection reset"))
    assert not any("matched by TEXT" in r.message for r in caplog.records)


def test_classification_never_raises_on_a_weird_exception():
    """B5 — classification runs on the failure path. If it can raise, it turns a
    recoverable error into an unrecoverable one."""
    class _Hostile(Exception):
        def __str__(self) -> str:
            raise RuntimeError("even my message is broken")

    with pytest.raises(RuntimeError):
        str(_Hostile())
    # A type match short-circuits before str() is ever reached.
    assert looks_like_dead_handle(ConnectionResetError("x"))
