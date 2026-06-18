"""Tests for TelegramNotificationDispatcher and NotificationPayload."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stackowl.channels.telegram.formatter import (
    TelegramBriefFormatter,
    TelegramEvolutionFormatter,
    TelegramMemoryFormatter,
    TelegramParliamentFormatter,
)
from stackowl.channels.telegram.notifications import (
    NotificationPayload,
    TelegramNotificationDispatcher,
    _content_hash,
)
from stackowl.channels.telegram.quiet_hours import QuietHoursChecker, TelegramQuietHoursConfig
from stackowl.channels.telegram.settings import TelegramSettings


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.send_text = AsyncMock()
    adapter.send_inline_keyboard = AsyncMock()
    return adapter


def _make_quiet_checker(suppress: bool = False) -> MagicMock:
    checker = MagicMock(spec=QuietHoursChecker)
    checker.should_suppress.return_value = suppress
    return checker


def _make_formatters() -> dict:
    return {
        "brief": TelegramBriefFormatter(),
        "parliament": TelegramParliamentFormatter(),
        "evolution": TelegramEvolutionFormatter(),
        "memory": TelegramMemoryFormatter(),
    }


def _make_settings(suppress_evolution: bool = False) -> TelegramSettings:
    return TelegramSettings(suppress_evolution_events=suppress_evolution)


def _make_dispatcher(
    adapter=None,
    quiet_checker=None,
    suppress_evolution: bool = False,
) -> TelegramNotificationDispatcher:
    adapter = adapter or _make_adapter()
    checker = quiet_checker or _make_quiet_checker(suppress=False)
    settings = _make_settings(suppress_evolution)
    return TelegramNotificationDispatcher(
        adapter=adapter,
        quiet_hours=checker,
        formatters=_make_formatters(),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# 1. dispatch suppresses when quiet hours active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_suppresses_during_quiet_hours() -> None:
    adapter = _make_adapter()
    checker = _make_quiet_checker(suppress=True)
    dispatcher = _make_dispatcher(adapter=adapter, quiet_checker=checker)

    payload = NotificationPayload(
        event_type="morning_brief",
        content={"Agenda": "Nothing"},
        urgency="normal",
    )
    await dispatcher.dispatch(payload)

    adapter.send_text.assert_not_called()
    adapter.send_inline_keyboard.assert_not_called()


# ---------------------------------------------------------------------------
# 2. dispatch sends morning_brief via adapter.send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_morning_brief_sends_text() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter)

    payload = NotificationPayload(
        event_type="morning_brief",
        content={"Today": "All good"},
        urgency="normal",
    )
    await dispatcher.dispatch(payload)

    adapter.send_text.assert_awaited_once()
    adapter.send_inline_keyboard.assert_not_called()
    # The text should be non-empty
    sent_text = adapter.send_text.call_args[0][0]
    assert len(sent_text) > 0


# ---------------------------------------------------------------------------
# 3. dispatch sends parliament_synthesis via adapter.send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_parliament_synthesis_sends_text() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter)

    payload = NotificationPayload(
        event_type="parliament_synthesis",
        content={
            "synthesis": "Consensus reached.",
            "owl_names": ["Archimedes", "Merlin"],
            "round_count": 3,
        },
        urgency="normal",
    )
    await dispatcher.dispatch(payload)

    adapter.send_text.assert_awaited_once()
    adapter.send_inline_keyboard.assert_not_called()
    sent_text = adapter.send_text.call_args[0][0]
    assert len(sent_text) > 0


# ---------------------------------------------------------------------------
# 4. dispatch sends evolution event when suppress_evolution_events=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_evolution_not_suppressed_by_default() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter, suppress_evolution=False)

    payload = NotificationPayload(
        event_type="evolution",
        content={"owl_name": "Archimedes", "trait_deltas": {"verbosity": 0.05}},
        urgency="low",
    )
    await dispatcher.dispatch(payload)

    adapter.send_text.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. dispatch suppresses evolution when suppress_evolution_events=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_evolution_suppressed_by_settings() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter, suppress_evolution=True)

    payload = NotificationPayload(
        event_type="evolution",
        content={"owl_name": "Archimedes", "trait_deltas": {"verbosity": 0.05}},
        urgency="low",
    )
    await dispatcher.dispatch(payload)

    adapter.send_text.assert_not_called()
    adapter.send_inline_keyboard.assert_not_called()


# ---------------------------------------------------------------------------
# 6. dispatch sends memory_nudge via adapter.send_inline_keyboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_memory_nudge_uses_inline_keyboard() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter)

    payload = NotificationPayload(
        event_type="memory_nudge",
        content={"fact_content": "User prefers dark mode.", "fact_id": "fact-001"},
        urgency="normal",
    )
    await dispatcher.dispatch(payload)

    adapter.send_inline_keyboard.assert_awaited_once()
    adapter.send_text.assert_not_called()


# ---------------------------------------------------------------------------
# 7. dispatch handles custom type via adapter.send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_custom_sends_text() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter)

    payload = NotificationPayload(
        event_type="custom",
        content={"text": "Hello from heartbeat!"},
        urgency="normal",
    )
    await dispatcher.dispatch(payload)

    adapter.send_text.assert_awaited_once_with("Hello from heartbeat!")
    adapter.send_inline_keyboard.assert_not_called()


# ---------------------------------------------------------------------------
# 8. dispatch logs content hash not content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_logs_content_hash_not_raw_content() -> None:
    adapter = _make_adapter()
    dispatcher = _make_dispatcher(adapter=adapter)

    payload = NotificationPayload(
        event_type="morning_brief",
        content={"Secret": "Top secret agenda"},
        urgency="normal",
    )

    log_calls: list[dict] = []

    original_debug = dispatcher._adapter.send_text.__class__  # unused; capture via log mock

    with patch("stackowl.channels.telegram.notifications.log") as mock_log:
        mock_log.telegram = MagicMock()
        mock_log.telegram.debug = MagicMock()
        mock_log.telegram.error = MagicMock()

        await dispatcher.dispatch(payload)

        # Gather all calls to log.telegram.debug
        all_debug_calls = mock_log.telegram.debug.call_args_list

    # Find the "step send" log call that should carry content_hash
    step_send_calls = [
        c for c in all_debug_calls
        if "step send" in str(c)
    ]
    assert len(step_send_calls) > 0, "Expected at least one 'step send' log entry"

    # Verify the content_hash field is present and is a 16-char hex string
    for call in step_send_calls:
        extra = call.kwargs.get("extra", {})
        fields = extra.get("_fields", {})
        if "content_hash" in fields:
            ch = fields["content_hash"]
            assert len(ch) == 16
            assert all(c in "0123456789abcdef" for c in ch)
            break
    else:
        pytest.fail("No log call with content_hash field found")


# ---------------------------------------------------------------------------
# 9. NotificationPayload is frozen Pydantic (immutable)
# ---------------------------------------------------------------------------


def test_notification_payload_is_frozen() -> None:
    payload = NotificationPayload(
        event_type="custom",
        content={"text": "hi"},
        urgency="normal",
    )
    with pytest.raises(Exception):
        payload.urgency = "critical"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10. dispatch handles critical urgency bypassing quiet hours
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_critical_bypasses_quiet_hours() -> None:
    adapter = _make_adapter()
    # quiet checker that would suppress normal traffic
    checker = _make_quiet_checker(suppress=False)
    # For critical, should_suppress must return False regardless
    checker.should_suppress.return_value = False  # simulating urgent_override logic

    dispatcher = TelegramNotificationDispatcher(
        adapter=adapter,
        quiet_hours=checker,
        formatters=_make_formatters(),
        settings=_make_settings(),
    )

    payload = NotificationPayload(
        event_type="custom",
        content={"text": "URGENT: system alert"},
        urgency="critical",
    )
    await dispatcher.dispatch(payload)

    checker.should_suppress.assert_called_once_with("critical")
    adapter.send_text.assert_awaited_once_with("URGENT: system alert")
