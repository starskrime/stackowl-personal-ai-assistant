"""F002/C-5 — browser.send_message selects the EXISTING chat by JID.

The old impl navigated to ``web.whatsapp.com/send?phone={jid.split('@')[0]}``,
which (a) opens a NEW-chat composer rather than the existing chat, and (b)
mishandles a group JID (``...@g.us``) by stripping everything after ``@`` — the
group id survives only up to the ``@``, so the group is lost.

The fix selects the existing chat keyed on the FULL JID for both
``@s.whatsapp.net`` (user) and ``@g.us`` (group) JIDs. These tests mock the
Playwright page and assert the group id is never lost to a phone composer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.whatsapp.browser import WhatsAppBrowserDriver
from stackowl.channels.whatsapp.session import WhatsAppSessionManager
from stackowl.channels.whatsapp.settings import WhatsAppSettings
from stackowl.config.test_mode import TestModeGuard

_GROUP_JID = "120363041234567890@g.us"
_USER_JID = "15551234567@s.whatsapp.net"


def _driver() -> tuple[WhatsAppBrowserDriver, MagicMock]:
    settings = WhatsAppSettings(
        allowed_phone_numbers=frozenset(["15551234567"]),
        session_dir="/tmp/test_wa_browser",
    )
    sm = WhatsAppSessionManager("/tmp/test_wa_browser")
    driver = WhatsAppBrowserDriver(settings, sm)
    page = MagicMock()
    page.goto = AsyncMock()
    # wait_for_selector returns a clickable element handle (async click).
    handle = MagicMock()
    handle.click = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=handle)
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.evaluate = AsyncMock(return_value=True)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.type = AsyncMock()
    driver._page = page
    return driver, page


def _all_strings(page: MagicMock) -> str:
    """Concatenate every positional/keyword string the page was called with."""
    chunks: list[str] = []
    for m in (page.goto, page.evaluate, page.fill, page.click, page.wait_for_selector):
        for c in m.await_args_list:
            chunks.extend(str(a) for a in c.args)
            chunks.extend(str(v) for v in c.kwargs.values())
    return " ".join(chunks)


@pytest.mark.asyncio
async def test_group_jid_not_stripped_to_phone_composer() -> None:
    TestModeGuard.deactivate()
    try:
        driver, page = _driver()
        await driver.send_message(_GROUP_JID, "hello group")
        seen = _all_strings(page)
        # The group id must survive intact somewhere (full JID-based selection).
        assert "120363041234567890" in seen
        # And it must NOT be addressed via the phone composer (which loses groups).
        assert "send?phone=120363041234567890" not in seen
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_user_jid_still_delivers() -> None:
    TestModeGuard.deactivate()
    try:
        driver, page = _driver()
        await driver.send_message(_USER_JID, "hello user")
        seen = _all_strings(page)
        assert "15551234567" in seen
        # The message body is typed/filled.
        assert page.fill.await_count + page.keyboard.type.await_count >= 1
    finally:
        TestModeGuard.deactivate()
