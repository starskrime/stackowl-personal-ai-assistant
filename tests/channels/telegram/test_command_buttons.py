from __future__ import annotations

from stackowl.channels.telegram.command_buttons import (
    TelegramCommandButtonResolver,
    register_command_button,
    set_command_button_message_id,
)
from stackowl.commands.response import CANCEL_SENTINEL, Action, CommandResponse


def test_register_command_button_returns_short_prefixed_id():
    action = Action(label="Remove", command="/provider remove acme")
    data = register_command_button(chat_id=123, action=action)

    assert data.startswith("cmd:")
    assert len(data.encode()) <= 64


async def test_resolver_dispatches_non_destructive_action():
    dispatched = {}

    class _FakeRegistry:
        async def dispatch(self, name, args, state):
            dispatched["name"] = name
            dispatched["args"] = args
            dispatched["session_id"] = state.session_id
            return CommandResponse(text="✓ removed")

    class _FakeAdapter:
        def __init__(self):
            self.sent = []

        async def send_text(self, text, *, chat_id=None):
            self.sent.append((chat_id, text))

    adapter = _FakeAdapter()
    registry = _FakeRegistry()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=registry)

    action = Action(label="Remove", command="/provider remove acme", destructive=False)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid1", data)

    assert dispatched["name"] == "provider"
    assert dispatched["args"] == "remove acme"
    assert dispatched["session_id"] == "555"
    assert adapter.sent == [(555, "✓ removed")]


async def test_resolver_shows_confirm_prompt_for_destructive_action_first_tap():
    class _FakeAdapter:
        def __init__(self):
            self.edited = []

        async def edit_message(self, *, chat_id, message_id, text, reply_markup=None):
            self.edited.append((chat_id, message_id, text))
            return True

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    action = Action(label="Remove", command="/provider remove acme", destructive=True)
    data = register_command_button(chat_id=555, action=action)
    # Simulate the real flow: adapter.send() would have backfilled the sent
    # message's id right after delivering the keyboard carrying this button.
    set_command_button_message_id(data, 42)

    await resolver.handle_callback("cbid2", data)

    assert any("Confirm" in text for _cid, _mid, text in adapter.edited)


async def test_resolver_cancel_sentinel_shows_cancelled_text():
    class _FakeAdapter:
        def __init__(self):
            self.edited = []

        async def edit_message(self, *, chat_id, message_id, text, reply_markup=None):
            self.edited.append((chat_id, message_id, text))
            return True

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    action = Action(label="Cancel", command=CANCEL_SENTINEL)
    data = register_command_button(chat_id=555, action=action)
    set_command_button_message_id(data, 43)

    await resolver.handle_callback("cbid3", data)

    assert any("Cancel" in text or "cancelled" in text.lower() for _cid, _mid, text in adapter.edited)


async def test_resolver_falls_back_to_fresh_send_when_message_id_unknown():
    """No backfilled message_id (e.g. registration outside adapter.send()) —
    the confirm prompt must still reach the user, just as a new message."""

    class _FakeAdapter:
        def __init__(self):
            self.keyboard_sent = []

        async def send_inline_keyboard(self, text, keyboard, chat_id=None, parse_mode="MarkdownV2"):
            self.keyboard_sent.append((chat_id, text))

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    action = Action(label="Remove", command="/provider remove acme", destructive=True)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid4", data)

    assert any("Confirm" in text for _cid, text in adapter.keyboard_sent)


def test_expired_or_unknown_button_is_ignored():
    class _FakeAdapter:
        pass

    resolver = TelegramCommandButtonResolver(adapter=_FakeAdapter(), registry=None)
    import asyncio

    asyncio.run(resolver.handle_callback("cbid5", "cmd:doesnotexist"))
    # No assertion beyond "did not raise" — an unknown/expired short_id is a
    # silent no-op (mirrors TelegramClarifyResolver's stale-tap handling).
