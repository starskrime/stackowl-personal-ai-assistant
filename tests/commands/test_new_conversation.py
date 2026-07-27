"""/new — the command itself had NO tests, which is why it shipped broken.

Found live 2026-07-27: `/new` called ``start_new_incarnation(state.session_key)``
with the CHANNEL-NATIVE id, while the stored lane is the composite
``owl:<owl>:<channel>:<chat_type>:<chat_id>``. It found nothing, replied "You're
already in a new conversation", and the conversation carried on unchanged. It had
never worked.

The store-side behaviour is covered by tests/sessions/test_new_command_reset.py.
What was missing — and what these add — is coverage of the COMMAND: that it asks
the store for the right thing, with the key it actually has, and that it tells
the user the truth about what happened.
"""

from __future__ import annotations

import pytest

from stackowl.commands.new_conversation import NewConversationCommand
from stackowl.pipeline.state import PipelineState

pytestmark = pytest.mark.asyncio

CHAT = "72055773"


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t-new-1",
        session_key=CHAT,          # channel-native — all a command ever has
        input_text="/new",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="command",
    )


class _RecordingStore:
    def __init__(self) -> None:
        self.requested: list[str] = []
        self.ended: list[str] = []

    async def request_new_incarnation(self, chat_key: str) -> None:
        self.requested.append(chat_key)

    async def start_new_incarnation(self, session_key: str, *a: object, **k: object) -> None:
        self.ended.append(session_key)  # the OLD path — must not be used
        return None


async def test_new_records_the_request_with_the_key_it_has() -> None:
    """The regression test for the actual defect: the command must hand the
    store the channel-native key and let resolution do the ending, rather than
    looking up a composite lane it cannot possibly know yet."""
    store = _RecordingStore()

    reply = await NewConversationCommand(store).handle("", _state())

    assert store.requested == [CHAT]
    assert store.ended == [], "must NOT call the old lane-lookup path"
    assert "new conversation" in reply.lower()


async def test_new_never_claims_a_boundary_it_did_not_get() -> None:
    """Honesty: a store that cannot record the request must not produce a reply
    implying the conversation ended."""

    class _BrokenStore:
        async def request_new_incarnation(self, chat_key: str) -> None:
            raise RuntimeError("db gone")

    reply = await NewConversationCommand(_BrokenStore()).handle("", _state())

    assert "could not" in reply.lower()


async def test_new_without_a_store_says_so() -> None:
    reply = await NewConversationCommand(None).handle("", _state())

    assert "not configured" in reply.lower()
