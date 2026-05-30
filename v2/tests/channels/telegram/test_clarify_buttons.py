"""Tests for the Telegram clarify inline-button path + callback resolver.

Two surfaces:

* ``TelegramChannelAdapter.send_clarify`` — builds one inline button per choice
  with ``callback_data`` ``clarify:{id}:{idx}``, targets ``int(session_id)``;
  a non-int session_id and a no-choices question both degrade to plain text.
* ``TelegramClarifyResolver.handle_callback`` — a tap maps ``idx`` → choice text
  and resolves the parked clarify via a REAL gateway (a parked
  ``wait_for_answer`` wakes with the chosen text). Malformed / stale / out-of-range
  taps are ignored.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.clarify import TelegramClarifyResolver
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.interaction.clarify_gateway import ClarifyGateway

USER_ID = 555444


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    """send_text / send_inline_keyboard are TestModeGuard-gated — open the gate."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _adapter() -> tuple[TelegramChannelAdapter, _FakeBot]:
    adapter = TelegramChannelAdapter(
        TelegramSettings(allowed_user_ids=frozenset({USER_ID}))
    )
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    return adapter, bot


# --------------------------------------------------------- send_clarify (adapter)


@pytest.mark.asyncio
async def test_send_clarify_builds_one_button_per_choice() -> None:
    adapter, bot = _adapter()

    await adapter.send_clarify(str(USER_ID), "Which colour?", ("red", "blue"), "CID123")

    assert len(bot.messages) == 1
    msg = bot.messages[0]
    assert msg["chat_id"] == USER_ID
    assert msg["text"] == "Which colour?"
    markup = msg["reply_markup"]
    assert markup is not None
    # Flatten the rows → callback_data is clarify:{id}:{idx} in choice order.
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["red", "blue"]
    assert [b.callback_data for b in buttons] == [
        "clarify:CID123:0",
        "clarify:CID123:1",
    ]


@pytest.mark.asyncio
async def test_send_clarify_escapes_markdownv2_in_question() -> None:
    # MAJOR-1 regression: the question is free-form (LLM) text sent with
    # parse_mode=MarkdownV2. Reserved chars (. ( ) _ - ! etc.) MUST be escaped or
    # Telegram rejects the send and the turn parks invisibly. Button LABELS are not
    # markdown-parsed, so choices stay raw.
    adapter, bot = _adapter()

    question = "Use option (a) or report_v2.md? Pick one - now!"
    await adapter.send_clarify(str(USER_ID), question, ("choice.one", "two"), "CID")

    assert len(bot.messages) == 1
    sent = bot.messages[0]["text"]
    # The reserved chars are backslash-escaped (escape_md), so the raw unescaped
    # sequences must NOT appear verbatim and the escaped forms must.
    assert "\\(a\\)" in sent and "report\\_v2\\.md" in sent and "now\\!" in sent
    # Choice button labels are delivered raw (not markdown-parsed by Telegram).
    buttons = [b for row in bot.messages[0]["reply_markup"].inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["choice.one", "two"]


@pytest.mark.asyncio
async def test_send_clarify_non_int_session_falls_back_to_text() -> None:
    adapter, bot = _adapter()
    # A live chat so the text fallback has somewhere to land.
    adapter._last_chat_id = USER_ID  # type: ignore[assignment]

    await adapter.send_clarify("not-an-int", "Which colour?", ("red", "blue"), "CID")

    assert len(bot.messages) == 1
    # No keyboard on the fallback — just the bare question as text.
    assert bot.messages[0]["reply_markup"] is None
    assert "Which colour?" in bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_send_clarify_no_choices_sends_plain_text() -> None:
    adapter, bot = _adapter()

    await adapter.send_clarify(str(USER_ID), "What is your goal?", (), "CID")

    assert len(bot.messages) == 1
    assert bot.messages[0]["reply_markup"] is None
    assert bot.messages[0]["text"] == "What is your goal?"
    # Still pinned to the asking user's chat.
    assert bot.messages[0]["chat_id"] == USER_ID


# ------------------------------------------------------ resolver (handle_callback)


@pytest.mark.asyncio
async def test_tap_resolves_parked_waiter_with_chosen_text() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask(
        str(USER_ID), "telegram", "Which colour?", choices=("red", "blue"), blocking=True,
    )
    resolver = TelegramClarifyResolver(gw)

    # Park a waiter (the clarify tool would be here mid-turn), then tap button 1.
    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)  # let it park on the event
    await resolver.handle_callback("cbid", f"clarify:{cid}:1")

    answer, timed_out = await waiter
    assert answer == "blue"  # choices[1]
    assert timed_out is False


@pytest.mark.asyncio
async def test_tap_resolve_before_park_still_delivers() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask(
        str(USER_ID), "telegram", "q?", choices=("red", "blue"), blocking=True,
    )
    resolver = TelegramClarifyResolver(gw)

    # Tap BEFORE the waiter parks — peek/try_resolve leave the entry for the waiter.
    await resolver.handle_callback("cbid", f"clarify:{cid}:0")
    answer, timed_out = await gw.wait_for_answer(cid, timeout=5.0)
    assert answer == "red"
    assert timed_out is False


@pytest.mark.asyncio
async def test_malformed_callback_ignored() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask(
        str(USER_ID), "telegram", "q?", choices=("red", "blue"), blocking=True,
    )
    resolver = TelegramClarifyResolver(gw)

    # Wrong prefix, too few parts, and a non-int index are all ignored.
    await resolver.handle_callback("c", "consent:abc:1")
    await resolver.handle_callback("c", f"clarify:{cid}")
    await resolver.handle_callback("c", f"clarify:{cid}:notanint")

    # The entry is untouched — still parkable / resolvable.
    entry = gw.peek(cid)
    assert entry is not None
    assert entry.answer is None
    assert entry.event is not None and not entry.event.is_set()


@pytest.mark.asyncio
async def test_stale_clarify_id_ignored() -> None:
    gw = ClarifyGateway()
    resolver = TelegramClarifyResolver(gw)
    # No such entry (peek → None) — a stale/superseded tap is a clean no-op.
    await resolver.handle_callback("c", "clarify:ghost-id:0")  # must not raise


@pytest.mark.asyncio
async def test_out_of_range_index_ignored() -> None:
    gw = ClarifyGateway()
    cid = await gw.ask(
        str(USER_ID), "telegram", "q?", choices=("red", "blue"), blocking=True,
    )
    resolver = TelegramClarifyResolver(gw)

    await resolver.handle_callback("c", f"clarify:{cid}:9")  # only 0,1 valid

    entry = gw.peek(cid)
    assert entry is not None
    assert entry.answer is None
    assert entry.event is not None and not entry.event.is_set()


@pytest.mark.asyncio
async def test_tap_resolves_by_id_independent_of_cap_one() -> None:
    """A tap resolves the entry whose clarify_id it CARRIES — not a session match.

    Register TWO blocking entries for the SAME session+channel directly in the
    gateway's pending map (bypassing the cap-one-per-session replace that ``ask``
    enforces). A session+channel re-match would resolve whichever entry comes
    first — possibly the wrong one. The id-keyed resolve must wake exactly the
    tapped entry's waiter with ITS choice text, leaving the other untouched.
    """
    import asyncio as _asyncio

    from stackowl.interaction.clarify_gateway import PendingClarify

    gw = ClarifyGateway()
    entry_a = PendingClarify(
        clarify_id="CID_A", session_id=str(USER_ID), channel="telegram",
        question="A?", choices=("a0", "a1"), event=_asyncio.Event(),
    )
    entry_b = PendingClarify(
        clarify_id="CID_B", session_id=str(USER_ID), channel="telegram",
        question="B?", choices=("b0", "b1"), event=_asyncio.Event(),
    )
    # Insert B FIRST so a session+channel re-match would hit B, not the tapped A.
    gw._pending["CID_B"] = entry_b
    gw._pending["CID_A"] = entry_a

    resolver = TelegramClarifyResolver(gw)
    waiter_a = asyncio.ensure_future(gw.wait_for_answer("CID_A", timeout=5.0))
    await asyncio.sleep(0)  # park on A's event

    await resolver.handle_callback("cbid", "clarify:CID_A:1")

    answer, timed_out = await waiter_a
    assert answer == "a1"  # A's choices[1], NOT b1
    assert timed_out is False
    # B is fully untouched — proves the resolve did not session-match the first.
    assert entry_b.answer is None
    assert not entry_b.event.is_set()


@pytest.mark.asyncio
async def test_blank_choice_in_middle_keeps_idx_alignment() -> None:
    """A blank choice in the middle must not shift the button indices.

    The gateway stores ("red", "", "blue"); the "blue" button must carry
    callback_data idx 2 (its index in entry.choices), so a tap resolves to
    "blue" — not "" — even though the blank produced no button.
    """
    adapter, bot = _adapter()

    await adapter.send_clarify(
        str(USER_ID), "Which?", ("red", "", "blue"), "CIDX",
    )

    assert len(bot.messages) == 1
    buttons = [b for row in bot.messages[0]["reply_markup"].inline_keyboard for b in row]
    # The blank choice produced no button; the surviving buttons keep their
    # ORIGINAL indices (0 and 2), never renumbered to 0 and 1.
    assert [b.text for b in buttons] == ["red", "blue"]
    assert [b.callback_data for b in buttons] == [
        "clarify:CIDX:0",
        "clarify:CIDX:2",
    ]

    # End-to-end: the "blue" button (idx 2) resolves to entry.choices[2] == "blue".
    gw = ClarifyGateway()
    cid = await gw.ask(
        str(USER_ID), "telegram", "Which?", choices=("red", "", "blue"), blocking=True,
    )
    resolver = TelegramClarifyResolver(gw)
    waiter = asyncio.ensure_future(gw.wait_for_answer(cid, timeout=5.0))
    await asyncio.sleep(0)
    await resolver.handle_callback("cbid", f"clarify:{cid}:2")
    answer, timed_out = await waiter
    assert answer == "blue"
    assert timed_out is False
