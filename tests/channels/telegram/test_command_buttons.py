from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from stackowl.channels.telegram.command_buttons import (
    TelegramCommandButtonResolver,
    _button_map,
    build_command_keyboard,
    register_command_button,
    set_command_button_message_id,
)
from stackowl.commands.response import CANCEL_SENTINEL, Action, CommandResponse


class _FakeMessage:
    """Stand-in for ``telegram.Message`` — carries only what the code reads."""

    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class _FakeAdapter:
    """Records every outbound call so tests can assert exact call counts/shapes.

    Mirrors :class:`TelegramChannelAdapter`'s public send surface used by the
    resolver: ``send_text``, ``send_text_or_actions`` (the real single-call
    chokepoint — see adapter.py), ``send_inline_keyboard``, ``edit_message``.
    """

    def __init__(self, next_message_id: int = 1000) -> None:
        self.sent_text: list[tuple[int | None, str]] = []
        self.sent_actions: list[tuple[int | None, str, tuple]] = []
        self.keyboard_sent: list[tuple[int | None, str]] = []
        self.edited: list[tuple[int, int, str]] = []
        self._next_message_id = next_message_id

    def _mint_id(self) -> int:
        mid = self._next_message_id
        self._next_message_id += 1
        return mid

    async def send_text(self, text, *, chat_id=None):
        self.sent_text.append((chat_id, text))
        return _FakeMessage(self._mint_id())

    async def send_text_or_actions(self, text, actions, *, chat_id=None):
        self.sent_actions.append((chat_id, text, actions))
        return await self.send_text(text, chat_id=chat_id)

    async def send_inline_keyboard(self, text, keyboard, chat_id=None, parse_mode="MarkdownV2"):
        self.keyboard_sent.append((chat_id, text))
        return _FakeMessage(self._mint_id())

    async def edit_message(self, *, chat_id, message_id, text, reply_markup=None):
        self.edited.append((chat_id, message_id, text))
        return True


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

    adapter = _FakeAdapter()
    registry = _FakeRegistry()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=registry)

    action = Action(label="Remove", command="/provider remove acme", destructive=False)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid1", data)

    assert dispatched["name"] == "provider"
    assert dispatched["args"] == "remove acme"
    assert dispatched["session_id"] == "555"
    # Went through the single-call chokepoint, not a bare send_text.
    assert adapter.sent_actions == [(555, "✓ removed", ())]
    assert adapter.sent_text == [(555, "✓ removed")]


async def test_resolver_dispatch_with_actions_sends_exactly_once():
    """Bug A regression: a dispatched reply that ITSELF carries actions must be
    delivered through ONE formatted call (send_text_or_actions), never sent
    twice — once raw via send_text and again (formatted) via the keyboard
    message."""

    reply_actions = (Action(label="Undo", command="/provider undo"),)

    class _FakeRegistry:
        async def dispatch(self, name, args, state):
            return CommandResponse(text="✓ removed", actions=reply_actions)

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=_FakeRegistry())

    action = Action(label="Remove", command="/provider remove acme", destructive=False)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid1b", data)

    # Exactly one call into the single-call chokepoint, carrying the actions.
    assert adapter.sent_actions == [(555, "✓ removed", reply_actions)]
    # The chokepoint itself only issued ONE underlying send (send_text here,
    # since _FakeAdapter.send_text_or_actions delegates to it) — never a
    # second, separately-formatted send of the same text.
    assert len(adapter.sent_text) == 1
    assert len(adapter.keyboard_sent) == 0


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


async def test_confirm_then_cancel_neutralizes_the_live_yes_button():
    """Bug B regression: Cancel must edit the confirm-prompt message away to
    "Cancelled." (never a fresh send) and both Yes/Cancel must be popped from
    the button map — WITHOUT any manual set_command_button_message_id call for
    the confirm keyboard's OWN buttons (that manual call is exactly what
    masked this bug in the older confirm-prompt tests above: they only
    backfilled the FIRST-level button, never the Yes/Cancel pair)."""

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    action = Action(label="Remove", command="/provider remove acme", destructive=True)
    data = register_command_button(chat_id=555, action=action)
    # This backfill mirrors what a REAL adapter.send() does for the ORIGINAL
    # prompt message only — it says nothing about the confirm keyboard's own
    # Yes/Cancel buttons, which is exactly the gap Bug B leaves open.
    set_command_button_message_id(data, 42)

    before_keys = set(_button_map.keys())
    await resolver.handle_callback("cbid-confirm", data)
    new_short_ids = set(_button_map.keys()) - before_keys

    # Confirm prompt rendered as an edit of the original message.
    assert adapter.edited == [(555, 42, adapter.edited[0][2])]
    assert "Confirm" in adapter.edited[0][2]

    # Exactly the two NEW buttons (Yes, Cancel) the confirm keyboard minted.
    assert len(new_short_ids) == 2
    live_entries = {sid: _button_map[sid] for sid in new_short_ids}
    yes_sid = next(sid for sid, e in live_entries.items() if e.action.command != CANCEL_SENTINEL)
    cancel_sid = next(sid for sid, e in live_entries.items() if e.action.command == CANCEL_SENTINEL)

    # Bug B assertion: both were backfilled to the confirm prompt's message_id
    # (== 42, the same message the edit above just wrote to) — WITHOUT the
    # test ever calling set_command_button_message_id for them itself.
    assert live_entries[yes_sid].message_id == 42
    assert live_entries[cancel_sid].message_id == 42

    # Tap Cancel — deliberately no manual backfill first.
    await resolver.handle_callback("cbid-cancel", f"cmd:{cancel_sid}")

    # Must be a SECOND edit of the SAME message (42), not a fresh send — a
    # fresh send would leave the original message's Yes button live/tappable.
    assert adapter.edited == [
        (555, 42, adapter.edited[0][2]),
        (555, 42, adapter.edited[1][2]),
    ]
    assert "cancelled" in adapter.edited[1][2].lower()
    assert adapter.sent_text == []  # no fresh-send fallback occurred

    # Both Yes and Cancel are gone from the map (_pop_valid pops on any
    # resolution) — a stale tap on the still-visible Yes button can't
    # re-trigger the destructive action.
    assert yes_sid not in _button_map
    assert cancel_sid not in _button_map

    # Direct proof: tapping the now-invalidated Yes button is a silent no-op,
    # not a second dispatch of the destructive command.
    await resolver.handle_callback("cbid-stale-yes", f"cmd:{yes_sid}")
    assert len(adapter.edited) == 2
    assert adapter.sent_text == []


async def test_multi_action_group_tapping_one_invalidates_siblings():
    """Plan D pre-work: an independent, non-destructive multi-choice row —
    e.g. the upcoming /onboarding autonomy step's [low][medium][high] — must
    match TUI's row-level ``_resolved`` freeze (message_bubble.ActionButtonRow):
    tapping ONE button invalidates ALL the others rendered in the same
    group, not just the tapped one. Before this fix, build_command_keyboard
    registered independent per-button entries with no sibling_ids, so the
    untapped buttons stayed live and dispatchable for the full 15-minute TTL."""
    dispatched: list[tuple[str, str]] = []

    class _FakeRegistry:
        async def dispatch(self, name, args, state):
            dispatched.append((name, args))
            return CommandResponse(text=f"✓ set to {args}")

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=_FakeRegistry())

    actions = (
        Action(label="Low", command="/autonomy set low", destructive=False),
        Action(label="Medium", command="/autonomy set medium", destructive=False),
        Action(label="High", command="/autonomy set high", destructive=False),
    )
    _keyboard, callback_ids = build_command_keyboard(777, actions)
    low_data, medium_data, high_data = callback_ids

    # All three siblings are live before any tap.
    assert all(cd[4:] in _button_map for cd in callback_ids)

    await resolver.handle_callback("cbid-medium", medium_data)
    assert dispatched == [("autonomy", "set medium")]

    # Stale tap on "low" — the button is still visible on the old message but
    # must now be a silent no-op, exactly like an expired button.
    await resolver.handle_callback("cbid-stale-low", low_data)
    assert dispatched == [("autonomy", "set medium")]
    assert adapter.sent_actions == [(777, "✓ set to set medium", ())]

    # Same for "high".
    await resolver.handle_callback("cbid-stale-high", high_data)
    assert dispatched == [("autonomy", "set medium")]
    assert len(adapter.sent_actions) == 1


async def test_single_action_group_has_no_siblings():
    """A lone replay button (no group) must not invalidate itself — regression
    guard for the empty-sibling_ids no-op case build_command_keyboard already
    handled before this generalization."""
    adapter = _FakeAdapter()

    class _FakeRegistry:
        async def dispatch(self, name, args, state):
            return CommandResponse(text="ok")

    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=_FakeRegistry())
    keyboard, callback_ids = build_command_keyboard(888, (Action(label="Retry", command="/retry"),))
    assert len(callback_ids) == 1

    await resolver.handle_callback("cbid-solo", callback_ids[0])
    assert adapter.sent_actions == [(888, "ok", ())]


def test_expired_or_unknown_button_is_ignored():
    """No chat_id available (default) — stays a true silent no-op, mirroring
    TelegramClarifyResolver's stale-tap handling."""

    class _FakeAdapter:
        pass

    resolver = TelegramCommandButtonResolver(adapter=_FakeAdapter(), registry=None)
    import asyncio

    asyncio.run(resolver.handle_callback("cbid5", "cmd:doesnotexist"))
    # No assertion beyond "did not raise" — an unknown/expired short_id with
    # no chat_id is a silent no-op (mirrors TelegramClarifyResolver's
    # stale-tap handling).


async def test_expired_button_sends_user_facing_message() -> None:
    """Task 14: a tap resolving to nothing (expired TTL or unknown short_id),
    when the router DID manage to extract a chat_id, must tell the user
    instead of silently swallowing the tap — see the ``entry is None`` branch
    in ``TelegramCommandButtonResolver.handle_callback``."""
    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    await resolver.handle_callback("cb-1", "cmd:doesnotexist", chat_id=12345)

    assert adapter.sent_text == [(12345, "This step expired — run /provider add to start again.")]


async def test_expired_button_without_chat_id_is_silent_noop() -> None:
    """When the router could NOT extract a chat_id (chat_id=None, the
    parameter's default), the expired-button message must not be attempted —
    there is nowhere to send it."""
    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    await resolver.handle_callback("cb-2", "cmd:doesnotexist")

    assert adapter.sent_text == []


# ---------------------------------------------------------------------------
# Task 16 — guided /provider add-flow driven through REAL button taps
#
# Task 15 (tests/journeys/commands/test_provider_command_journey.py) drove
# this same guided catalog add-flow via direct CommandRegistry.dispatch calls.
# This test exercises the identical flow through the ACTUAL Telegram button
# mechanism: build_command_keyboard mints each step's `cmd:{short_id}`, and
# TelegramCommandButtonResolver.handle_callback resolves the tap — the exact
# path a real inline-keyboard tap takes in production (orchestrator.py wires
# the resolver against CommandRegistry.instance()).
#
# The token step (add-token) has no button — the user types the raw secret as
# a text message — so, like Task 15, that single step is driven by a direct
# registry.dispatch call; every other step (the initial add, add-pick,
# add-model, add-tier) is driven by a simulated tap.
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """In-memory stand-in for the ``keyring`` module (mirrors Task 15's
    fixture) — the AI-provider HTTP boundary is the only thing this journey
    mocks; the OS keychain is a local system dependency the test must not
    touch."""

    _store: dict[str, str] = {}

    @classmethod
    def set_password(cls, service: str, _user: str, secret: str) -> None:
        cls._store[service] = secret

    @classmethod
    def get_password(cls, service: str, _user: str) -> str | None:
        return cls._store.get(service)


def _find_action(actions: tuple, prefix: str) -> Action:
    match = next((a for a in actions if a.command.startswith(prefix)), None)
    assert match is not None, f"expected an action starting with {prefix!r}, got: {actions}"
    return match


async def test_provider_guided_add_flow_via_real_button_taps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """add -> tap add-pick groq -> add-token groq <TOKEN> -> tap add-model
    -> tap add-tier, all through TelegramCommandButtonResolver.handle_callback,
    mirroring Task 15's direct-dispatch journey but through real button taps."""
    import sys

    from stackowl.commands.assembly import CommandDeps, register_all_commands
    from stackowl.commands.registry import CommandRegistry
    from stackowl.config.settings import Settings
    from stackowl.providers.registry import ProviderRegistry
    from tests._story_6_7_helpers import make_state

    CommandRegistry.reset()
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode", lambda *a, **kw: None
    )

    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(yaml.dump({"test_mode": True, "providers": []}), encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))

    _FakeKeyring._store.clear()
    monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring)
    monkeypatch.setattr(
        "stackowl.providers.model_discovery.list_models",
        AsyncMock(return_value=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]),
    )

    deps = CommandDeps(event_bus=MagicMock())
    registry = CommandRegistry.instance()
    register_all_commands(deps, registry=registry)

    chat_id = 424242
    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=registry)

    # Step 1 — bare "/provider add" (typed, no prior button) browses the
    # catalog; register its actions as real buttons.
    browse = await registry.dispatch("provider", "add", make_state())
    _keyboard, browse_ids = build_command_keyboard(chat_id, browse.actions)
    pick_data = browse_ids[browse.actions.index(_find_action(browse.actions, "/provider add-pick groq"))]

    # Step 2 — TAP add-pick groq. Non-destructive -> resolver dispatches
    # through the real CommandRegistry and delivers the reply via the
    # single-call chokepoint (send_text_or_actions).
    await resolver.handle_callback("cb-pick", pick_data, chat_id=chat_id)
    pick_chat_id, pick_text, pick_actions = adapter.sent_actions[-1]
    assert pick_chat_id == chat_id
    assert "add-token groq" in pick_text
    assert pick_actions == ()  # token step has no button — user types it

    # Step 3 — supply the token via a direct dispatch (there is no button for
    # free-text secret entry); drives the mocked live-discovery call.
    raw_token = "sk-button-journey-RAW-TOKEN-001"  # noqa: S105 — test fixture value
    token_reply = await registry.dispatch(
        "provider", f"add-token groq {raw_token}", make_state()
    )
    assert raw_token not in token_reply.text
    model_action = _find_action(token_reply.actions, "/provider add-model groq")
    _, _, catalog_name, model, api_key_ref = model_action.command.split()
    assert catalog_name == "groq"
    assert api_key_ref.startswith("keychain:")
    assert _FakeKeyring._store[api_key_ref.removeprefix("keychain:")] == raw_token

    # Step 4 — TAP add-model. Register token_reply's actions as buttons first
    # (mirrors production: the reply's actions are rendered as a keyboard on
    # the message the resolver just sent).
    _keyboard, model_ids = build_command_keyboard(chat_id, token_reply.actions)
    model_data = model_ids[token_reply.actions.index(model_action)]
    n_sent_before = len(adapter.sent_actions)
    await resolver.handle_callback("cb-model", model_data, chat_id=chat_id)
    assert len(adapter.sent_actions) == n_sent_before + 1
    model_chat_id, model_text, model_actions = adapter.sent_actions[-1]
    assert model_chat_id == chat_id
    tier_action = _find_action(
        model_actions, f"/provider add-tier {catalog_name} {model} {api_key_ref}"
    )
    _, _, _, _, _, tier = tier_action.command.split()

    # Step 5 — TAP add-tier: the LAST step of the guided flow.
    _keyboard, tier_ids = build_command_keyboard(chat_id, model_actions)
    tier_data = tier_ids[model_actions.index(tier_action)]
    await resolver.handle_callback("cb-tier", tier_data, chat_id=chat_id)
    final_chat_id, final_text, _final_actions = adapter.sent_actions[-1]
    assert final_chat_id == chat_id
    assert "✓" in final_text
    assert "applied immediately" in final_text.lower()

    # Assert #1 — persisted correctly to stackowl.yaml.
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    persisted = next(p for p in data["providers"] if p["name"] == catalog_name)
    assert persisted["default_model"] == model
    assert persisted["tiers"] == [tier]
    assert persisted["api_key"] == api_key_ref
    assert persisted["enabled"] is True

    # Assert #2 — a fresh ProviderRegistry built from that YAML can route to it.
    live_settings = Settings()
    live_registry = ProviderRegistry.from_settings(live_settings)
    assert live_registry.get(catalog_name).name == catalog_name
    assert live_registry.get_by_tier(tier).name == catalog_name
