"""The highest-frequency Telegram call was the one path with no flood guard.

THE INCIDENT THIS RULE COMES FROM, recorded in the adapter itself at line 164:
"Live incident (2026-07-19): Telegram issued a ~10h RetryAfter flood-control ban"
after every send path hammered the API with no backoff. The fix added
`_flood_wait_remaining()` (skip a call already known to fail) and
`_note_flood_ban()` (learn a ban from any RetryAfter seen).

MEASURED 2026-09-04: three send paths consult that guard. `edit_message` — the
call that drives the live progress status message at roughly ONE PER SECOND FOR
EVERY TURN, making it the busiest Telegram caller in the platform — consulted
neither half:

  * it never checked `_flood_wait_remaining()`, so during a ban it kept calling a
    banned API once a second per active turn;
  * its `except Exception` swallowed `RetryAfter` as "edit failed — fail open",
    so a ban OBSERVED on this path was never recorded and the other three paths
    never learned about it.

Same rule, one case short — and short on the loudest case. The defect is latent
rather than firing (no flood lines in any current log), which is exactly why it
needed a test: nothing would notice until the next ban.

Fail-open stays fail-open. A skipped edit returns False, as a failed edit already
did, so the cosmetic progress line degrades and no turn or consent decision is
lost.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from telegram.error import RetryAfter

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings

pytestmark = pytest.mark.asyncio


def _adapter() -> TelegramChannelAdapter:
    return TelegramChannelAdapter(
        TelegramSettings(bot_token="test_token_x" * 3, allowed_user_ids=frozenset({42}))
    )


class _Bot:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls = 0
        self._raises = raises

    async def edit_message_text(self, **_kw: Any) -> None:
        self.calls += 1
        if self._raises is not None:
            raise self._raises


def _wire(adapter: TelegramChannelAdapter, bot: _Bot) -> None:
    adapter._bot_app = type("App", (), {"bot": bot})()


async def test_an_active_ban_SKIPS_the_edit_entirely() -> None:
    """The wasted round-trip that fed the original ban. No API call at all."""
    adapter, bot = _adapter(), _Bot()
    _wire(adapter, bot)
    adapter._flood_until = time.monotonic() + 300

    assert await adapter.edit_message(chat_id=1, message_id=2, text="x") is False
    assert bot.calls == 0, "the edit reached a banned API"


async def test_a_RetryAfter_SEEN_here_is_RECORDED_for_every_other_path() -> None:
    """The second half. A ban observed on the busiest path must teach the rest."""
    adapter, bot = _adapter(), _Bot(raises=RetryAfter(120))
    _wire(adapter, bot)
    assert adapter._flood_wait_remaining() == 0.0

    assert await adapter.edit_message(chat_id=1, message_id=2, text="x") is False
    assert adapter._flood_wait_remaining() > 0, (
        "a RetryAfter seen on the edit path was swallowed — the other send paths "
        "still believe there is no ban"
    )


async def test_with_no_ban_the_edit_goes_through() -> None:
    """Vacuity control: a guard that skipped everything would satisfy both tests
    above while silently ending the live progress message."""
    adapter, bot = _adapter(), _Bot()
    _wire(adapter, bot)

    assert await adapter.edit_message(chat_id=1, message_id=2, text="x") is True
    assert bot.calls == 1


async def test_not_modified_is_still_a_benign_no_op() -> None:
    """Regression pin: the pre-existing benign branch must survive the change."""
    adapter, bot = _adapter(), _Bot(raises=RuntimeError("Message is not modified"))
    _wire(adapter, bot)

    assert await adapter.edit_message(chat_id=1, message_id=2, text="x") is False
    assert adapter._flood_wait_remaining() == 0.0, "a benign no-op recorded a ban"


@pytest.mark.tripwire
def test_EVERY_bot_api_caller_checks_and_records() -> None:
    """A ninth send path must not arrive without the guard.

    This predicate is decidable, which is why it is a gate and the map-freshness
    report is not: it asks whether a method that calls a NAMED Bot API function
    also names two other functions, all within one file's AST. No prose, no
    heuristic.

    Measured 2026-09-04 before the fix: five of eight callers checked the ban and
    five recorded one — and the three gaps were the busiest paths in the platform
    (the ~1/s progress edit, the ~4s typing reissue, and the consent prompt's own
    send_message).
    """
    import ast
    import inspect

    from stackowl.channels.telegram import adapter as tg

    src = inspect.getsource(tg)
    api = (
        "send_message", "edit_message_text", "delete_message",
        "send_chat_action", "answer_callback_query",
    )
    offenders: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = ast.get_source_segment(src, node) or ""
        if not any(f"bot.{name}(" in body for name in api):
            continue
        checks = "_flood_blocked(" in body or "_flood_wait_remaining()" in body
        records = "_note_flood_ban(" in body
        if not (checks and records):
            offenders.append(
                f"{node.name} (checks={checks}, records={records})"
            )
    assert not offenders, (
        "these Telegram API callers do not both consult the flood ban and learn "
        f"from a RetryAfter: {offenders}"
    )
