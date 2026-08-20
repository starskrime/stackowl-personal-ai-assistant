"""Clicking Approve must always do something visible.

BAKIR, 2026-08-20: "I see approve button i am clicking but no reaction."

MEASURED. The consent prompt waits ``_DEFAULT_TIMEOUT_SECONDS`` and then::

    02:08:30  [telegram] consent.prompt: timed out — denying (fail closed)
              {'tool': 'owl_build', 'timeout_s': 120.0}
    02:08:30  [consent] policy.request: exit  {'decision': 'deny',
              'reason': 'user_denied'}

Two minutes is shorter than a phone notification often takes to reach someone. And
the INFO line that a resolved click writes — ``consent.handle_callback: resolved`` —
has NEVER appeared in any log, so no click has ever landed inside that window.

What a late click met::

    if pending is None or pending.future.done():
        log.telegram.debug("no live request — ignored")
        return

Silently ignored, at DEBUG (production runs at INFO, so invisible), with the button
still sitting there. From Bakir's side: tap, nothing, forever. And the platform had
already recorded it as ``user_denied`` — blaming him for a refusal he never made, the
same shape as the session_key bug earlier.

TWO FIXES, and the second matters more than the first. The window becomes 1200s at
his instruction. But ANY window can expire, so an expired click must SAY SO — a dead
button that swallows taps is indistinguishable from a broken platform, which is
exactly how it felt.
"""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.consent import (
    _DEFAULT_TIMEOUT_SECONDS,
    TelegramConsentPrompter,
)

pytestmark = pytest.mark.asyncio


class _Adapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int | None]] = []

    async def send_inline_keyboard(self, text, keyboard, chat_id=None,
                                   parse_mode="MarkdownV2"):  # noqa: ANN001, ANN201
        self.sent.append((text, chat_id))
        return None

    async def edit_message(self, chat_id, message_id, text, *, reply_markup=None):  # noqa: ANN001, ANN201
        return True


class TestTheWindowIsLongEnoughToReachAPhone:
    async def test_the_default_window_is_twenty_minutes(self) -> None:
        """120s expired before Bakir could reach the button. He asked for 1200."""
        assert _DEFAULT_TIMEOUT_SECONDS == 1200.0


class TestAnExpiredClickTellsTheUser:
    async def test_an_unknown_request_id_is_answered_not_swallowed(self) -> None:
        """The whole complaint. A tap that does nothing is indistinguishable from a
        broken platform."""
        adapter = _Adapter()
        prompter = TelegramConsentPrompter(adapter)

        await prompter.handle_callback("cb1", "consent:missing-rid:once", chat_id=99)

        assert adapter.sent, "an expired approval must not be silently ignored"
        text, chat = adapter.sent[0]
        assert chat == 99
        assert "expire" in text.lower() or "no longer" in text.lower()

    async def test_it_says_what_to_do_next(self) -> None:
        """"It expired" without "ask again" leaves him exactly as stuck."""
        adapter = _Adapter()
        prompter = TelegramConsentPrompter(adapter)

        await prompter.handle_callback("cb1", "consent:gone:once", chat_id=99)

        assert "again" in adapter.sent[0][0].lower()

    async def test_a_non_consent_callback_is_still_ignored_quietly(self) -> None:
        """Other features share the callback stream — clarify:, cmd:, vtx:. Replying
        to those would spam him on every button in the product."""
        adapter = _Adapter()
        prompter = TelegramConsentPrompter(adapter)

        await prompter.handle_callback("cb1", "clarify:abc:yes", chat_id=99)

        assert adapter.sent == []

    async def test_no_chat_id_cannot_crash_the_handler(self) -> None:
        adapter = _Adapter()
        prompter = TelegramConsentPrompter(adapter)

        await prompter.handle_callback("cb1", "consent:gone:once")

        assert adapter.sent == []

    async def test_a_failing_send_never_raises_into_the_router(self) -> None:
        """This runs on the callback path; raising would break every other button."""
        class _Boom(_Adapter):
            async def send_inline_keyboard(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise RuntimeError("telegram down")

        prompter = TelegramConsentPrompter(_Boom())

        await prompter.handle_callback("cb1", "consent:gone:once", chat_id=99)
