"""Notification.target widening (C1 / F104) — channel-native recipient token.

``target_chat_id: int|None`` could only hold a telegram chat id; slack needs a
str channel id. The field is renamed ``target: str|int|None`` and ``target_chat_id``
is kept as a deprecated read alias for one release (minimal-change / no-break).
"""

from __future__ import annotations

from stackowl.notifications.router import Notification


def test_target_accepts_int_telegram_chat_id() -> None:
    n = Notification(message="hi", urgency="normal", category="morning_brief", target=12345)
    assert n.target == 12345


def test_target_accepts_str_slack_channel_id() -> None:
    n = Notification(message="hi", urgency="normal", category="morning_brief", target="C0ABC")
    assert n.target == "C0ABC"


def test_target_chat_id_alias_reads_target() -> None:
    """Back-compat: existing callers reading ``target_chat_id`` still work."""
    n = Notification(message="hi", urgency="normal", category="x", target=999)
    assert n.target_chat_id == 999


def test_target_chat_id_alias_is_none_when_unset() -> None:
    n = Notification(message="hi", urgency="normal", category="x")
    assert n.target is None
    assert n.target_chat_id is None


def test_target_chat_id_constructor_alias_still_accepted() -> None:
    """Existing callers that pass ``target_chat_id=`` keep working (no break)."""
    n = Notification(message="hi", urgency="normal", category="x", target_chat_id=42)
    assert n.target == 42
    assert n.target_chat_id == 42
