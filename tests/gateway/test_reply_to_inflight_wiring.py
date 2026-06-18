"""STEER-1 (F060) — reply-to-inflight is structurally wired end-to-end.

F060: ``route_inflight_message(..., is_reply_to_inflight=False)`` was hardwired
False at the orchestrator call site (``IngressMessage`` carried no reply-link
field), so the structural reply-to-the-running-turn STEER path
(``parse_explicit_signal`` honours ``is_reply_to_inflight``) was unreachable in
production. These tests assert the WIRING that makes it reachable:

  1. ``IngressMessage`` carries an optional, byte-compatible ``is_reply`` flag
     (default False) — every existing constructor is unaffected.
  2. The orchestrator's pure resolver ``resolve_reply_to_inflight`` maps a
     channel reply-to-the-bot (``is_reply=True``) to a structural STEER ONLY
     when a turn is in-flight for the session (otherwise it is a normal new
     turn — a reply to an OLD bot message with nothing running is not a steer).
  3. Driven through the REAL ``route_inflight_message`` → ``TurnRouter`` →
     ``try_steer`` seam, a reply-to-inflight FOLDS the body into the running
     turn's mailbox and does NOT spawn a duplicate queued-new turn — even when
     the LLM classifier would have said NEW (the structural signal short-circuits
     the classifier entirely, zero LLM cost).
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.gateway.inflight_router import (
    InflightAction,
    route_inflight_message,
)
from stackowl.gateway.scanner import IngressMessage
from stackowl.gateway.turn_registry import TurnRegistry
from stackowl.gateway.turn_router import ExplicitSignal, TurnRouter
from stackowl.startup.orchestrator import resolve_reply_to_inflight


class _NewClassifier:
    """A classifier that would route every UNSIGNALED message to NEW."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def is_steer(self, *, running_ask: str, message: str) -> bool:
        self.calls.append((running_ask, message))
        return False  # would say NEW; the structural signal must override


def test_ingress_message_reply_field_defaults_false_and_is_byte_compatible() -> None:
    # Existing positional/keyword constructors are unaffected (no reply arg).
    msg = IngressMessage(text="hi", session_id="s1", channel="telegram", trace_id="t1")
    assert msg.is_reply is False
    # And the new flag is settable.
    reply = IngressMessage(
        text="hi", session_id="s1", channel="telegram", trace_id="t2", is_reply=True
    )
    assert reply.is_reply is True


def test_resolve_reply_to_inflight_only_when_turn_running() -> None:
    # A reply while a turn is running → structural STEER.
    assert resolve_reply_to_inflight(is_reply=True, turn_running=True) is True
    # A reply with NOTHING running is just a normal message (a reply to an old
    # bot message) — never a spurious structural steer.
    assert resolve_reply_to_inflight(is_reply=True, turn_running=False) is False
    # A non-reply is never a structural steer.
    assert resolve_reply_to_inflight(is_reply=False, turn_running=True) is False
    assert resolve_reply_to_inflight(is_reply=False, turn_running=False) is False


@pytest.mark.asyncio
async def test_reply_to_inflight_folds_and_does_not_duplicate() -> None:
    """A reply-to-inflight folds the body — even though the classifier says NEW."""
    reg = TurnRegistry()
    classifier = _NewClassifier()
    router = TurnRouter(classifier)  # type: ignore[arg-type]
    task = asyncio.create_task(asyncio.sleep(5.0))
    turn = await reg.register(
        "r1", session_id="s1", task=task, target=None, original_input="build the parser"
    )
    try:
        # The orchestrator resolves the structural flag from the IngressMessage.
        is_reply_to_inflight = resolve_reply_to_inflight(
            is_reply=True, turn_running=reg.running("s1") is not None
        )
        assert is_reply_to_inflight is True

        outcome = await asyncio.wait_for(
            route_inflight_message(
                router=router,
                registry=reg,
                running=turn,
                text="also use tabs",
                session_id="s1",
                request_id_new="r2",
                target=None,
                is_reply_to_inflight=is_reply_to_inflight,
            ),
            2.0,
        )
        # Structural STEER short-circuits the classifier (zero LLM cost).
        assert outcome.action is InflightAction.HANDLED
        assert outcome.signal is ExplicitSignal.STEER
        assert classifier.calls == []
        # The body folded into the running turn's mailbox — no duplicate queued-new.
        folded = turn.steering_mailbox.get_nowait()
        assert folded == "also use tabs"
        assert reg.pop_next("s1") is None  # NOTHING enqueued as a new turn
    finally:
        task.cancel()
