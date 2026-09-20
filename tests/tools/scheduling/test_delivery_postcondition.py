"""ADR-1 — send tools declare a DeliveryAck post-condition routed through the authority.

The transport's ``delivery_status`` (the deliverer's actual return) is an observation
DISTINCT from the success bool the model sees — so a delivery's truth flows through the
one AcceptanceAuthority (F-29/F-30/F-25). Flag OFF ⇒ the seam is skipped ⇒ byte-identical;
the existing self-stamp (verified=status=='delivered') is kept, so a batched/deferred send
stays verified=False either way.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import stackowl.tools.base as base_mod
from stackowl.channels.registry import ChannelRegistry
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.acceptance_authority import DeliveryAck
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.scheduling._delivery_postcondition import delivery_post_condition
from stackowl.tools.scheduling.send_message import SendMessageTool
from stackowl.tools.verification import is_trustworthy_success

# --- the pure helper -----------------------------------------------------------


def test_helper_delivered_is_acked() -> None:
    out = json.dumps({"record": {"target": "telegram", "delivery_status": "delivered"}})
    pc = delivery_post_condition(out)
    assert isinstance(pc, DeliveryAck)
    assert pc.acked is True
    assert pc.channel == "telegram"


def test_helper_batched_is_not_acked() -> None:
    out = json.dumps({"record": {"target": "cli", "delivery_status": "batched"}})
    pc = delivery_post_condition(out)
    assert isinstance(pc, DeliveryAck)
    assert pc.acked is False


def test_helper_no_delivery_is_none() -> None:
    # action='list' record (no delivery_status) ⇒ nothing to verify.
    out = json.dumps({"record": {"action": "list", "channels": []}})
    assert delivery_post_condition(out) is None
    # malformed / empty output ⇒ no opinion, never raises.
    assert delivery_post_condition("") is None
    assert delivery_post_condition("not json") is None


# --- through the real tool + the __call__ seam ---------------------------------


class _FakeAdapter:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_text(self, text: str) -> None:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _channels() -> Any:
    reg = ChannelRegistry.instance()
    reg.reset()
    reg.register(_FakeAdapter("telegram"))
    reg.register(_FakeAdapter("cli"))
    yield reg
    reg.reset()


@pytest.fixture
def authority_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_mod, "_acceptance_authority_enabled", lambda: True)


@pytest.fixture
def authority_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_mod, "_acceptance_authority_enabled", lambda: False)


async def _call(
    tool: SendMessageTool, status: str, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> Any:
    """Invoke the tool through __call__ (exercises the post_condition seam).

    Story 4.8 (AD-1) — ``_deliver`` now submits ``messaging.send_message``
    through ``commands/spec/submit.py::submit_command`` rather than calling
    ``proactive_deliverer.deliver`` directly, and an owner-attended
    irreversible send ALWAYS parks for step-up (no owner carve-out) — there
    is no longer a live path where this tool's own ``execute()`` observes a
    synchronous "delivered" outcome. ``submit_command`` is stubbed here to
    script the delivery-command OUTCOME this test needs (mirrors
    ``notifications/commands.py``'s own handler result shape), so the real
    subject under test — the tool's own post_condition/AcceptanceAuthority
    wiring — is exercised exactly as before.
    """
    from stackowl.commands.spec.context import CommandOutcome
    from stackowl.commands.spec.submit import CommandSubmission

    async def _fake_submit_command(*_a: Any, **_kw: Any) -> CommandSubmission:
        return CommandSubmission(
            command_id="stub-delivery-postcondition", task_id="stub-task",
            outcome=CommandOutcome(success=(status != "failed"), result={"delivery_status": status}),
        )

    monkeypatch.setattr("stackowl.commands.spec.submit.submit_command", _fake_submit_command)
    services = StepServices(db_pool=object())  # type: ignore[arg-type]
    stoken = set_services(services)
    ttoken = TraceContext.start(
        session_key="s", trace_id="t", interactive=True, channel="cli"
    )
    try:
        return await tool(action="send", text="hi", target="cli", **kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(stoken)


@pytest.mark.asyncio
async def test_delivered_verified_true_when_authority_on(
    authority_on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _call(SendMessageTool(), "delivered", monkeypatch)
    assert result.success is True
    assert result.verified is True  # authority observed the transport ack
    assert is_trustworthy_success(result.success, result.verified) is True


@pytest.mark.asyncio
async def test_batched_stays_unverified_when_authority_on(
    authority_on: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _call(SendMessageTool(), "batched", monkeypatch)
    assert result.success is True
    assert result.verified is False  # queued, not delivered — not trustworthy
    assert is_trustworthy_success(result.success, result.verified) is False


@pytest.mark.asyncio
async def test_flag_off_delivered_is_byte_identical(
    authority_off: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flag explicitly forced OFF ⇒ ADR-1 seam skipped ⇒ the self-stamp's True is
    # demoted by the existing F-25 block to None (exactly pre-ADR behavior), still
    # trustworthy. (acceptance_authority now defaults True — see settings.py — so
    # this must force the flag off itself rather than assume an ambient default.)
    result = await _call(SendMessageTool(), "delivered", monkeypatch)
    assert result.verified is None
    assert is_trustworthy_success(result.success, result.verified) is True
