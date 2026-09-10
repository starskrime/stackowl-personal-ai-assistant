"""An approval that was ANSWERED must never be reported as expired.

Bakir, 2026-09-10: "I do not like when approval is expiring. It kills all
platform vibe. Acces should wait till user appprove and continue the same hob and
not give error like That approval request has expired, so the tap did nothing.
Ask me again and I'll re-request it."

MEASURED on the run he was describing, and it is the opposite of what he was
told. His tap RESOLVED at 01:46:51 — `consent.handle_callback: resolved`,
`consent.prompt: exit` — and `skill_manage.execute: entry` fired in the same
second, storing and registering the skills. The job continued and finished. Two
further taps on that same request, at 01:47:02 and 01:47:16, each received "That
approval request has expired, so the tap did nothing. Ask me again and I'll
re-request it."

EVERY CLAUSE OF THAT WAS FALSE: it had not expired, the tap HAD done something,
and asking again would have re-run a consequential action that had already run.
At 01:47:26 the platform then failed to deliver even that message (Telegram
ConnectTimeout).

THE CAUSE IS ONE PREDICATE. ``pending is None or pending.future.done()`` is
reached by three different histories — answered, timed out, and never seen by
this process — and a single sentence was written for all three, asserting the
worst of them. Nothing recorded which had happened, so nothing COULD tell them
apart: an answered request and a lost one are both simply absent from
``_pending``.

Same shape as DEBT-277 in the same session: a message asserting a cause it never
checked.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.tools.consent import ConsentRequest, ConsentScope


class _Adapter:
    """Records what the user would actually see."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[int, int, str]] = []

    async def send_inline_keyboard(self, text, keyboard, *, chat_id, parse_mode=None):
        self.sent.append(text)
        return type("M", (), {"message_id": 4242})()

    async def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append((chat_id, message_id, text))


def _request() -> ConsentRequest:
    return ConsentRequest(
        tool_name="skill_manage",
        channel="telegram",
        session_key="12345",
        summary="write a skill",
        reply_target=12345,
    )


async def _answer_then_tap_again(scope: ConsentScope) -> _Adapter:
    """Drive the real sequence: prompt, resolve by tap, then tap again."""
    adapter = _Adapter()
    prompter = TelegramConsentPrompter(adapter)
    task = asyncio.create_task(prompter.prompt(_request()))
    await asyncio.sleep(0)  # let prompt() send and register the pending entry
    rid = next(iter(prompter._pending))  # noqa: SLF001
    await prompter.handle_callback("cb", f"consent:{rid}:{scope.value}", chat_id=12345)
    assert await task == scope, "the decision itself must still resolve the turn"
    adapter.sent.clear()
    # THE SECOND TAP — the one that produced the false message.
    await prompter.handle_callback("cb2", f"consent:{rid}:{scope.value}", chat_id=12345)
    return adapter


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_second_tap_on_an_approved_request_is_not_called_expired() -> None:
    """The exact sequence he hit."""
    adapter = await _answer_then_tap_again(ConsentScope.ONCE)
    assert adapter.sent, "the second tap said nothing at all"
    reply = adapter.sent[-1]
    assert "expired" not in reply.lower(), reply
    assert "did nothing" not in reply.lower(), reply
    assert "ask me again" not in reply.lower(), (
        "telling him to re-request an action that already ran is how it gets done "
        f"twice: {reply}"
    )
    assert "already approved" in reply.lower(), reply


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_second_tap_on_a_declined_request_says_declined() -> None:
    """A refusal is not an expiry either, and must not invite a retry."""
    adapter = await _answer_then_tap_again(ConsentScope.DENY)
    reply = adapter.sent[-1]
    assert "declined" in reply.lower(), reply
    assert "expired" not in reply.lower(), reply


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_a_request_this_process_never_saw_says_exactly_that() -> None:
    """THE VACUITY CONTROL, and the honest version of the old message.

    A tap for an unknown rid is what a restart looks like — `_pending` is an
    in-memory dict, and CodeWatcher re-execs the core on most boots here. The
    reply must say the request is gone WITHOUT claiming it expired, because this
    process cannot know that. If this test passed while the two above failed, the
    fix would be a blanket rewording rather than a distinction.
    """
    adapter = _Adapter()
    prompter = TelegramConsentPrompter(adapter)
    await prompter.handle_callback("cb", "consent:deadbeef:once", chat_id=12345)
    reply = adapter.sent[-1]
    assert "no longer have that request" in reply.lower(), reply
    assert "expired" not in reply.lower(), reply
    assert "nothing ran" in reply.lower(), reply


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_the_decision_memory_is_bounded() -> None:
    """Failure shape #4: anything that only appends will poison its reader.

    Past the bound the reply degrades to "I no longer have that request", which
    is TRUE, rather than to the expiry claim this file exists to delete.
    """
    from stackowl.channels.telegram.consent import _DECIDED_MEMORY

    prompter = TelegramConsentPrompter(_Adapter())
    for i in range(_DECIDED_MEMORY + 50):
        prompter._remember_decision(f"rid-{i}", ConsentScope.ONCE)  # noqa: SLF001
    assert len(prompter._decided) == _DECIDED_MEMORY  # noqa: SLF001
    assert "rid-0" not in prompter._decided  # noqa: SLF001
    assert f"rid-{_DECIDED_MEMORY + 49}" in prompter._decided  # noqa: SLF001
