"""F005 merge-gate — WhatsApp text-only consent (numbered reply round-trip).

WhatsApp has no buttons. A consequential action posts a numbered "reply N to
approve / deny" prompt and PARKS a Future; the user's NEXT inbound message
(``resolve_reply``, called by the WhatsApp loop before the clarify pump) resolves
the consent decision. Approve and deny both round-trip; an unparseable reply
re-prompts (stays parked) rather than guessing.

Fail-closed regression: the prompter targets the initiating session's JID
(resolved from the adapter map); when no JID resolves it denies without a send.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.channels.whatsapp.consent import WhatsAppConsentPrompter
from stackowl.tools.consent import ConsentRequest, ConsentScope


class _FakeAdapter:
    """Minimal adapter surface the prompter needs: resolve_target + send_text."""

    def __init__(self, targets: dict[str, str]) -> None:
        self._targets = targets
        self.sent: list[tuple[str, str]] = []

    def resolve_target(self, session_id: str) -> str | int | None:
        return self._targets.get(session_id)

    async def send_text(self, text: str, *, target: str | None = None) -> None:
        self.sent.append((target or "", text))


def _req(session_id: str = "whatsapp:abc", relax: bool = True) -> ConsentRequest:
    return ConsentRequest(
        tool_name="shell", channel="whatsapp", session_id=session_id,
        summary="rm -rf /tmp/x", allow_relaxation=relax,
    )


@pytest.mark.asyncio
async def test_numbered_reply_approve_round_trips() -> None:
    adapter = _FakeAdapter({"whatsapp:abc": "1555@s.whatsapp.net"})
    prompter = WhatsAppConsentPrompter(adapter)  # type: ignore[arg-type]
    task = asyncio.create_task(prompter.prompt(_req()))
    await asyncio.sleep(0)  # let prompt() send + park

    assert adapter.sent  # a numbered prompt was sent
    # "1" = approve (the first numbered option).
    consumed = await prompter.resolve_reply("whatsapp:abc", "1")
    assert consumed is True
    scope = await asyncio.wait_for(task, timeout=2.0)
    assert scope == ConsentScope.SESSION  # relaxation → session grant


@pytest.mark.asyncio
async def test_numbered_reply_deny_round_trips() -> None:
    adapter = _FakeAdapter({"whatsapp:abc": "1555@s.whatsapp.net"})
    prompter = WhatsAppConsentPrompter(adapter)  # type: ignore[arg-type]
    task = asyncio.create_task(prompter.prompt(_req()))
    await asyncio.sleep(0)
    # "2" = deny.
    consumed = await prompter.resolve_reply("whatsapp:abc", "2")
    assert consumed is True
    scope = await asyncio.wait_for(task, timeout=2.0)
    assert scope == ConsentScope.DENY_SESSION


@pytest.mark.asyncio
async def test_unparseable_reply_stays_parked() -> None:
    """A non-numbered reply does NOT resolve consent (no silent guess)."""
    adapter = _FakeAdapter({"whatsapp:abc": "1555@s.whatsapp.net"})
    prompter = WhatsAppConsentPrompter(adapter)  # type: ignore[arg-type]
    task = asyncio.create_task(prompter.prompt(_req()))
    await asyncio.sleep(0)
    consumed = await prompter.resolve_reply("whatsapp:abc", "maybe later")
    assert consumed is False  # not consumed → runs as a normal message
    assert not task.done()  # consent still parked
    # Clean up the parked task so the test doesn't leak.
    await prompter.resolve_reply("whatsapp:abc", "2")
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_no_jid_fails_closed_without_send() -> None:
    adapter = _FakeAdapter({})  # no target for the session
    prompter = WhatsAppConsentPrompter(adapter)  # type: ignore[arg-type]
    scope = await prompter.prompt(_req("whatsapp:unknown"))
    assert scope == ConsentScope.DENY
    assert adapter.sent == []  # never sent — no guessed JID


@pytest.mark.asyncio
async def test_resolve_reply_no_pending_is_passthrough() -> None:
    """resolve_reply with nothing parked returns False (ordinary message)."""
    adapter = _FakeAdapter({})
    prompter = WhatsAppConsentPrompter(adapter)  # type: ignore[arg-type]
    assert await prompter.resolve_reply("whatsapp:abc", "1") is False
