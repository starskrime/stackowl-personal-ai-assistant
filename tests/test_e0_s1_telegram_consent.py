"""E0-S1 — Telegram consent prompter round-trip.

A consent prompt is sent as an inline keyboard; the acting coroutine suspends
on a Future that the inline-button callback resolves. Timeout / send failure
fail closed (deny).
"""

from __future__ import annotations

import asyncio

from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.tools.consent import ConsentRequest, ConsentScope


class _FakeMessage:
    """Minimal stand-in for telegram.Message (message_id + chat.id)."""

    def __init__(self, message_id: int, chat_id: int) -> None:
        self.message_id = message_id
        self.chat = type("_Chat", (), {"id": chat_id})()


class _FakeAdapter:
    def __init__(
        self, *, fail: bool = False, edit_raises: bool = False, message_id: int = 777
    ) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.sent_chat_ids: list[int | None] = []
        self.sent_parse_modes: list[str | None] = []
        self.sent_event = asyncio.Event()
        self._fail = fail
        self._edit_raises = edit_raises
        self._message_id = message_id
        # Records of edit_message calls: (chat_id, message_id, text, reply_markup)
        self.edits: list[tuple[int, int, str, object | None]] = []

    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict,
        chat_id: int | None = None,
        parse_mode: str | None = "MarkdownV2",
    ) -> object | None:
        if self._fail:
            raise RuntimeError("transport down")
        self.sent.append((text, keyboard))
        self.sent_chat_ids.append(chat_id)
        self.sent_parse_modes.append(parse_mode)
        self.sent_event.set()
        return _FakeMessage(self._message_id, chat_id if chat_id is not None else 0)

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: object | None = None,
    ) -> bool:
        if self._edit_raises:
            raise RuntimeError("edit transport down")
        self.edits.append((chat_id, message_id, text, reply_markup))
        return True


def _buttons(keyboard: dict) -> list[dict]:
    rows = keyboard.get("inline_keyboard", [])
    return [btn for row in rows for btn in row]


def _cd_for(keyboard: dict, scope: str) -> str:
    for btn in _buttons(keyboard):
        if btn["callback_data"].endswith(f":{scope}"):
            return btn["callback_data"]
    raise AssertionError(f"no button for scope {scope} in {keyboard}")


def _req(allow_relaxation: bool = True) -> ConsentRequest:
    # session_id must be a numeric Telegram chat id: the B1 fix resolves the
    # target chat from it and fails closed (no send) on a non-numeric value, so a
    # placeholder like "s1" would make the inline keyboard never go out.
    return ConsentRequest(
        tool_name="danger", channel="telegram", session_id="100",
        summary="run the dangerous thing", allow_relaxation=allow_relaxation,
    )


async def test_prompt_resolves_to_scope_on_callback() -> None:
    """Approve button on a relaxable request carries 'session' scope."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.sent_event.wait()
        # allow_relaxation=True → approve button is 'session'
        cd = _cd_for(adapter.sent[0][1], "session")
        await prompter.handle_callback("cb-1", cd)

    results = await asyncio.gather(prompter.prompt(_req()), _resolve())
    assert results[0] is ConsentScope.SESSION


async def test_prompt_resolves_once_scope_for_excluded_tool() -> None:
    """For always-ask tools (allow_relaxation=False) the approve button is 'once'."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.sent_event.wait()
        await prompter.handle_callback("cb-2", _cd_for(adapter.sent[0][1], "once"))

    results = await asyncio.gather(prompter.prompt(_req(allow_relaxation=False)), _resolve())
    assert results[0] is ConsentScope.ONCE


async def test_prompt_times_out_to_deny() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    assert await prompter.prompt(_req()) is ConsentScope.DENY


async def test_prompt_send_failure_denies() -> None:
    adapter = _FakeAdapter(fail=True)
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    assert await prompter.prompt(_req()) is ConsentScope.DENY


async def test_keyboard_always_has_exactly_two_buttons() -> None:
    """New 2-button contract: always Approve + Deny, never 3 or 4 buttons."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=True))
    assert len(_buttons(adapter.sent[0][1])) == 2  # approve(session) + deny(deny_session)

    adapter2 = _FakeAdapter()
    prompter2 = TelegramConsentPrompter(adapter2, timeout_seconds=0.05)
    await prompter2.prompt(_req(allow_relaxation=False))
    assert len(_buttons(adapter2.sent[0][1])) == 2  # approve(once) + deny(deny_session)


async def test_approve_button_scope_depends_on_allow_relaxation() -> None:
    """With relaxation=True the approve callback carries 'session'; without it 'once'."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=True))
    cds = [b["callback_data"] for b in _buttons(adapter.sent[0][1])]
    # First button is Approve → scope must be 'session' (relaxation allowed)
    assert cds[0].endswith(":session"), f"Expected approve:session, got {cds[0]}"
    # Second button is Deny → scope must be 'deny_session'
    assert cds[1].endswith(":deny_session"), f"Expected deny:deny_session, got {cds[1]}"


async def test_approve_scope_is_once_when_relaxation_not_allowed() -> None:
    """always-ask tools: approve must use 'once', not 'session'."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=False))
    cds = [b["callback_data"] for b in _buttons(adapter.sent[0][1])]
    assert cds[0].endswith(":once"), f"Expected approve:once, got {cds[0]}"
    assert cds[1].endswith(":deny_session"), f"Expected deny:deny_session, got {cds[1]}"


async def test_excluded_keyboard_has_no_session_or_window_button() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=False))
    cds = [b["callback_data"] for b in _buttons(adapter.sent[0][1])]
    assert not any(c.endswith(":session") or c.endswith(":window") for c in cds)


async def test_callback_for_unknown_request_is_ignored() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    # resolving a request id that was never issued must not raise
    await prompter.handle_callback("cb-x", "consent:999:once")


async def test_prompt_targets_requesting_users_chat() -> None:
    """B1 fix — the consent prompt goes to the initiating user's chat (session_id)."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    req = ConsentRequest(tool_name="danger", channel="telegram", session_id="424242", summary="x")
    await prompter.prompt(req)  # times out to deny, but records the target chat
    assert adapter.sent_chat_ids == [424242]
    # Consent prompts are sent as PLAIN TEXT (parse_mode=None) so an unescaped
    # command/path can never 400 on MarkdownV2 entity parsing → spurious deny.
    assert adapter.sent_parse_modes == [None]


async def test_non_numeric_session_fails_closed() -> None:
    """If the target chat can't be resolved from session_id, fail closed fast."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    req = ConsentRequest(tool_name="danger", channel="telegram", session_id="not-an-id", summary="x")
    assert await prompter.prompt(req) is ConsentScope.DENY
    assert adapter.sent == []  # never attempted a send


async def test_request_ids_are_unique_and_non_sequential() -> None:
    """M3 fix — request ids are unguessable, not a 1,2,3 counter."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.02)
    await prompter.prompt(_req())
    await prompter.prompt(_req())
    rids = [b["callback_data"].split(":")[1] for kb in [s[1] for s in adapter.sent] for b in _buttons(kb)[:1]]
    assert len(set(rids)) == 2
    assert not all(r.isdigit() and len(r) <= 2 for r in rids)  # not a tiny counter


async def test_malformed_callback_does_not_resolve() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    # garbage callback data is ignored; prompt still times out to deny
    await prompter.handle_callback("cb-y", "garbage")
    assert await prompter.prompt(_req()) is ConsentScope.DENY


# ---------------------------------------------------------------------------
# UX: on tap, the original message is rewritten to the decision + buttons gone
# ---------------------------------------------------------------------------


async def _drive(
    adapter: _FakeAdapter,
    scope: str,
    prompter: TelegramConsentPrompter,
    allow_relaxation: bool = True,
) -> ConsentScope:
    async def _resolve() -> None:
        await adapter.sent_event.wait()
        await prompter.handle_callback("cb", _cd_for(adapter.sent[0][1], scope))

    results = await asyncio.gather(prompter.prompt(_req(allow_relaxation)), _resolve())
    return results[0]


async def test_tap_edits_message_to_allow_symbol() -> None:
    """Approve on an always-ask tool → ONCE scope → ✅ symbol."""
    adapter = _FakeAdapter(message_id=4242)
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    # allow_relaxation=False → approve button scope is 'once'
    scope = await _drive(adapter, "once", prompter, allow_relaxation=False)
    assert scope is ConsentScope.ONCE
    assert len(adapter.edits) == 1
    chat_id, message_id, text, reply_markup = adapter.edits[0]
    assert chat_id == 100  # session_id of _req()
    assert message_id == 4242
    assert reply_markup is None  # keyboard removed
    assert text.startswith("✅")
    assert "run the dangerous thing" in text  # the ORIGINAL action summary


async def test_tap_edits_message_to_session_symbol() -> None:
    """Approve on a relaxable request → SESSION scope → 🔒 symbol."""
    adapter = _FakeAdapter(message_id=11)
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    scope = await _drive(adapter, "session", prompter)
    assert scope is ConsentScope.SESSION
    assert adapter.edits[0][1] == 11
    assert adapter.edits[0][3] is None
    assert adapter.edits[0][2].startswith("🔒")


async def test_tap_edits_message_to_deny_session_symbol() -> None:
    """Deny button now carries deny_session scope → ❌ symbol."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    scope = await _drive(adapter, "deny_session", prompter)
    assert scope is ConsentScope.DENY_SESSION
    assert adapter.edits[0][3] is None
    assert adapter.edits[0][2].startswith("❌")


async def test_tap_edits_message_to_deny_session_symbol_excluded_tool() -> None:
    """Deny on always-ask tool → deny_session → ❌ symbol."""
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    scope = await _drive(adapter, "deny_session", prompter, allow_relaxation=False)
    assert scope is ConsentScope.DENY_SESSION
    assert adapter.edits[0][2].startswith("❌")


async def test_edit_failure_does_not_lose_the_decision() -> None:
    """Fail-open UX: if edit_message RAISES, the consent decision is still returned."""
    adapter = _FakeAdapter(edit_raises=True)
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    # allow_relaxation=False → approve button is 'once'
    scope = await _drive(adapter, "once", prompter, allow_relaxation=False)
    # The future was resolved BEFORE the (failing) edit — decision is never lost.
    assert scope is ConsentScope.ONCE
    assert adapter.edits == []  # edit raised, recorded nothing
