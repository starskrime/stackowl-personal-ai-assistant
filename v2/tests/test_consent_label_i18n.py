"""Consent button labels must render as real text, not raw i18n keys.

Regression test for the clobber bug: install_default_translations() (called on
the serve/Telegram path) must not wipe consent keys registered separately.
After the fix, the consolidated _EN table includes all consent keys, so a
single install_default_translations() call is sufficient.
"""

from __future__ import annotations

import asyncio

from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.tools.consent import ConsentRequest
from stackowl.tui.i18n import clear_translations, localize, register_translations
from stackowl.tui.i18n_strings import install_default_translations

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _buttons(keyboard: dict) -> list[dict]:
    rows = keyboard.get("inline_keyboard", [])
    return [btn for row in rows for btn in row]


class _FakeAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self._event = asyncio.Event()

    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict,
        chat_id: int | None = None,
        parse_mode: str | None = None,
    ) -> object:
        self.sent.append((text, keyboard))
        self._event.set()
        return type("_Msg", (), {"message_id": 1, "chat": type("_C", (), {"id": chat_id})()})()

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: object | None = None,
    ) -> bool:
        return True


def _req(allow_relaxation: bool = True) -> ConsentRequest:
    return ConsentRequest(
        tool_name="danger",
        channel="telegram",
        session_id="100",
        summary="run the dangerous thing",
        allow_relaxation=allow_relaxation,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_consent_buttons_show_real_labels_not_keys() -> None:
    """After install_default_translations() alone, every consent button text is real.

    This is the clobber regression: previously the consent keys were registered
    in orchestrator.py BEFORE install_default_translations() clobbered them.
    The fix embeds consent keys inside _EN so they survive one-shot registration.
    """
    clear_translations()
    install_default_translations()

    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=True))

    assert adapter.sent, "No keyboard was sent"
    keyboard = adapter.sent[0][1]
    buttons = _buttons(keyboard)
    assert len(buttons) == 4, f"Expected 4 buttons, got {len(buttons)}: {buttons}"

    for btn in buttons:
        label = btn["text"]
        assert not label.startswith("consent."), (
            f"Button label is a raw i18n key: {label!r}"
        )
        # The real check: known raw keys must NOT appear as labels
        raw_keys = {
            "consent.btn.approve_once",
            "consent.btn.deny",
            "consent.btn.approve_session",
            "consent.btn.trust_window",
        }
        assert label not in raw_keys, f"Button has raw key as label: {label!r}"


async def test_consent_labels_without_relaxation_are_real() -> None:
    """Consent buttons with allow_relaxation=False also show real labels."""
    clear_translations()
    install_default_translations()

    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=False))

    assert adapter.sent, "No keyboard was sent"
    buttons = _buttons(adapter.sent[0][1])
    assert len(buttons) == 2, f"Expected 2 buttons, got {len(buttons)}"
    raw_keys = {"consent.btn.approve_once", "consent.btn.deny"}
    for btn in buttons:
        assert btn["text"] not in raw_keys, f"Raw key as label: {btn['text']!r}"
        assert not btn["text"].startswith("consent."), f"Raw key as label: {btn['text']!r}"


def test_register_translations_merges_not_replaces() -> None:
    """A later partial registration must not wipe keys from an earlier full one.

    This directly tests the merge semantics of register_translations().
    """
    clear_translations()
    install_default_translations()

    # Verify a consent key from the full table is present
    assert localize("consent.btn.deny") == "🚫 Deny", (
        f"consent.btn.deny not in table: got {localize('consent.btn.deny')!r}"
    )

    # Now register a partial table (as the old orchestrator code did)
    register_translations("en", {"x.y": "Z"})

    # The pre-existing consent key must still be there (merge, not replace)
    assert localize("consent.btn.deny") == "🚫 Deny", (
        "register_translations clobbered pre-existing key consent.btn.deny — "
        "should merge, not replace"
    )
    # And the new key is added
    assert localize("x.y") == "Z"


def test_every_consent_prompter_key_lives_in_catalog() -> None:
    """Single source of truth: every label key the prompter renders must exist in _EN.

    Guards against re-orphaning consent copy: if a future change moves these
    keys back out of the consolidated table (the original bug), this fails.
    """
    from stackowl.channels.telegram.consent import _LABEL_KEYS
    from stackowl.tui.i18n_strings import _EN

    required = {"consent.prompt.title", *(_LABEL_KEYS.values())}
    missing = sorted(k for k in required if k not in _EN)
    assert not missing, f"Consent keys missing from the consolidated _EN catalog: {missing}"
