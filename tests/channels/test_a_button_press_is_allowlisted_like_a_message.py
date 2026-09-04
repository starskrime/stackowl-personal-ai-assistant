"""A button press is gated by the allow-list, exactly like a message.

MEASURED 2026-09-04. Every channel gated inbound MESSAGES and none gated TAPS.

    telegram   is_authorized in _handle_update, _handle_document and voice —
               three paths — while CallbackQueryHandler(router.route) was
               registered with NO filter at all
    discord    the button seam received the whole `interaction` and never read
               `interaction.user`
    slack      the action seam received `body` and never read body["user"]["id"]

In all three the presser's identity was in hand and reached no decision. The
routers dispatch `consent:` and `clarify:`, so the tap is not cosmetic — it
RESOLVES A PENDING APPROVAL. The Telegram adapter describes itself as "DM +
group support, allowlist-gated", and a group is precisely where someone who is
not on the allow-list can see the prompt and press its button.

The front door was locked and the side door was not.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from stackowl.channels.callback_authz import press_is_authorized

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# The shared decision. Fails closed on every unknown.
# --------------------------------------------------------------------------


def test_an_unknown_presser_is_refused() -> None:
    assert press_is_authorized("telegram", None, lambda _uid: True) is False


def test_a_missing_allowlist_is_refused() -> None:
    """No list to check against is not permission — it is the absence of one."""
    assert press_is_authorized("telegram", 42, None) is False


def test_a_predicate_that_RAISES_refuses() -> None:
    def boom(_uid: Any) -> bool:
        raise RuntimeError("allow-list unavailable")

    assert press_is_authorized("telegram", 42, boom) is False


def test_a_stranger_is_refused_and_the_owner_is_not() -> None:
    """Vacuity control: a check that refuses everyone would pass every test above."""
    allowed = frozenset({42})
    assert press_is_authorized("telegram", 7, lambda uid: uid in allowed) is False
    assert press_is_authorized("telegram", 42, lambda uid: uid in allowed) is True


# --------------------------------------------------------------------------
# Telegram — the seam is the handler registration itself.
# --------------------------------------------------------------------------


def _tg_adapter(allowed: frozenset[int]) -> Any:
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.telegram.settings import TelegramSettings

    return TelegramChannelAdapter(
        TelegramSettings(bot_token="test_token_x" * 3, allowed_user_ids=allowed)
    )


def _tg_update(user_id: int | None) -> Any:
    from_user = None if user_id is None else types.SimpleNamespace(id=user_id)
    return types.SimpleNamespace(callback_query=types.SimpleNamespace(from_user=from_user))


def _attach_and_capture(adapter: Any, router: Any) -> Any:
    """Register the router and hand back the callback PTB would actually call."""
    captured: list[Any] = []
    adapter._bot_app = types.SimpleNamespace(add_handler=captured.append)
    adapter.attach_callback_router(router)
    assert captured, "no handler was registered"
    return captured[0].callback


class _SpyRouter:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def route(self, update: Any, context: Any) -> None:
        self.calls.append(update)


async def test_telegram_refuses_a_press_from_someone_not_on_the_allowlist() -> None:
    """This is the defect. Before the fix the router was registered bare, so this
    press reached `consent:` and resolved a pending approval."""
    adapter = _tg_adapter(frozenset({42}))
    router = _SpyRouter()
    handler = _attach_and_capture(adapter, router)

    await handler(_tg_update(9999), None)

    assert router.calls == [], "a stranger's tap reached the consent router"


async def test_telegram_still_routes_the_owners_press() -> None:
    """The other half, and the one that matters for not breaking him: the fix
    must not silence the operator's own buttons."""
    adapter = _tg_adapter(frozenset({42}))
    router = _SpyRouter()
    handler = _attach_and_capture(adapter, router)

    await handler(_tg_update(42), None)

    assert len(router.calls) == 1


async def test_telegram_refuses_a_press_with_no_identifiable_sender() -> None:
    adapter = _tg_adapter(frozenset({42}))
    router = _SpyRouter()
    handler = _attach_and_capture(adapter, router)

    await handler(_tg_update(None), None)

    assert router.calls == []


# --------------------------------------------------------------------------
# Every seam, structurally — so a fourth channel cannot skip the rule.
# --------------------------------------------------------------------------


def test_every_callback_seam_consults_the_allowlist() -> None:
    """A router invocation with no allow-list check beside it is the whole bug.

    Named per file rather than counted, because the failure mode is a NEW seam
    added later without the check — exactly how these three came to exist.
    """
    import inspect

    from stackowl.channels.discord import callbacks as discord_callbacks
    from stackowl.channels.telegram import adapter as telegram_adapter
    from stackowl.startup import orchestrator

    for label, module in (
        ("telegram adapter", telegram_adapter),
        ("discord button seam", discord_callbacks),
        ("slack action seam", orchestrator),
    ):
        src = inspect.getsource(module)
        assert "press_is_authorized" in src, (
            f"{label} invokes a callback router without consulting the allow-list"
        )
