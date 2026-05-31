"""E0-S1 — Telegram consent prompter round-trip.

A consent prompt is sent as an inline keyboard; the acting coroutine suspends
on a Future that the inline-button callback resolves. Timeout / send failure
fail closed (deny).
"""

from __future__ import annotations

import asyncio

from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.tools.consent import ConsentRequest, ConsentScope


class _FakeAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.sent_chat_ids: list[int | None] = []
        self.sent_parse_modes: list[str | None] = []
        self.sent_event = asyncio.Event()
        self._fail = fail

    async def send_inline_keyboard(
        self,
        text: str,
        keyboard: dict,
        chat_id: int | None = None,
        parse_mode: str | None = "MarkdownV2",
    ) -> None:
        if self._fail:
            raise RuntimeError("transport down")
        self.sent.append((text, keyboard))
        self.sent_chat_ids.append(chat_id)
        self.sent_parse_modes.append(parse_mode)
        self.sent_event.set()


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
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.sent_event.wait()
        cd = _cd_for(adapter.sent[0][1], "once")
        await prompter.handle_callback("cb-1", cd)

    results = await asyncio.gather(prompter.prompt(_req()), _resolve())
    assert results[0] is ConsentScope.ONCE


async def test_prompt_resolves_session_scope() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.sent_event.wait()
        await prompter.handle_callback("cb-2", _cd_for(adapter.sent[0][1], "session"))

    results = await asyncio.gather(prompter.prompt(_req()), _resolve())
    assert results[0] is ConsentScope.SESSION


async def test_prompt_times_out_to_deny() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    assert await prompter.prompt(_req()) is ConsentScope.DENY


async def test_prompt_send_failure_denies() -> None:
    adapter = _FakeAdapter(fail=True)
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    assert await prompter.prompt(_req()) is ConsentScope.DENY


async def test_keyboard_offers_relaxation_only_when_allowed() -> None:
    adapter = _FakeAdapter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.prompt(_req(allow_relaxation=True))
    assert len(_buttons(adapter.sent[0][1])) == 4  # once/deny/session/window

    adapter2 = _FakeAdapter()
    prompter2 = TelegramConsentPrompter(adapter2, timeout_seconds=0.05)
    await prompter2.prompt(_req(allow_relaxation=False))
    assert len(_buttons(adapter2.sent[0][1])) == 2  # once/deny only


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
