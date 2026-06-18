"""Task 3: ClarifyGateway.ask(deliver=False) — register-without-deliver.

Two tests:
1. deliver=False registers the pending entry (peekable/resolvable) but does NOT
   call adapter.send_clarify.
2. deliver=True (the default) still calls send_clarify — byte-identical to the
   existing behavior.
"""
import pytest
from stackowl.interaction.clarify_gateway import ClarifyGateway


class _SpyAdapter:
    def __init__(self): self.sent = []

    async def send_clarify(self, session_id, question, choices, clarify_id):
        self.sent.append((session_id, question, clarify_id))


@pytest.mark.asyncio
async def test_deliver_false_registers_but_does_not_send():
    gw = ClarifyGateway()
    spy = _SpyAdapter()
    gw.register_adapter("cli", spy)
    cid = await gw.ask("sess", "cli", "What kind of pictures?", deliver=False)
    assert spy.sent == []                                   # not delivered
    pending = gw.peek_for_session("sess", "cli")            # but registered
    assert pending is not None and pending.clarify_id == cid
    assert pending.question == "What kind of pictures?"
    assert pending.event is None                            # turn-yield


@pytest.mark.asyncio
async def test_deliver_true_default_still_sends():
    gw = ClarifyGateway()
    spy = _SpyAdapter()
    gw.register_adapter("cli", spy)
    await gw.ask("sess", "cli", "Q?")
    assert len(spy.sent) == 1
