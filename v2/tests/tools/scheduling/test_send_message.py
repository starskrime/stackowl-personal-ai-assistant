"""Tests for SendMessageTool — agent outbound text over the channel registry (E7-S3).

A fake ProactiveDeliverer records each ``deliver(Notification)`` so the tests can
assert the target channel, the message body and the (clamped) ``normal`` urgency.
``execute`` is called directly — unit scope bypasses the registry's consent gate
(the gate round-trip is proven in the SMOKE step). The channel registry singleton
is populated with fake adapters and reset in a fixture.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.infra.trace import TraceContext
from stackowl.notifications.router import Notification
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.scheduling.send_message import SendMessageTool

pytestmark = pytest.mark.asyncio

_TRACE = "trace-sm-1"


class _FakeDeliverer:
    """Records deliver() calls and returns a scripted DeliveryStatus."""

    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[Notification] = []

    async def deliver(self, notification: Notification) -> str:
        self.calls.append(notification)
        return self.status


class _FakeAdapter:
    """Minimal ChannelAdapter stand-in — only ``channel_name`` is read here."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    # The deliverer (not under test here) would call send_text; never reached in
    # unit scope because we inject a fake deliverer.
    async def send_text(self, text: str) -> None:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _channels() -> Any:
    """Register two fake channels in the registry singleton; reset after each test."""
    reg = ChannelRegistry.instance()
    reg.reset()
    reg.register(_FakeAdapter("telegram"))
    reg.register(_FakeAdapter("cli"))
    yield reg
    reg.reset()


def _decode(output: str) -> dict[str, Any]:
    return json.loads(output)["record"]


async def _run(
    tool: SendMessageTool,
    *,
    deliverer: Any,
    channel: str | None = "telegram",
    session_id: str | None = "sess-sm",
    trace_id: str | None = _TRACE,
    **kwargs: Any,
) -> Any:
    services = StepServices(proactive_deliverer=deliverer)
    stoken = set_services(services)
    ttoken = TraceContext.start(
        session_id=session_id, trace_id=trace_id, interactive=True, channel=channel
    )
    try:
        return await tool.execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(stoken)


# --------------------------------------------------------------------------- tests


async def test_send_explicit_target_delivers_once() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        action="send",
        text="hello there",
        target="cli",
    )
    assert result.success is True
    assert len(deliverer.calls) == 1
    sent = deliverer.calls[0]
    assert sent.message == "hello there"
    assert sent.channel_name == "cli"  # explicit target honored
    assert sent.category == "agent_message"
    assert sent.urgency == "normal"
    record = _decode(result.output)
    assert record["target"] == "cli"
    assert record["delivery_status"] == "delivered"


async def test_target_omitted_defaults_to_session_channel() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        channel="telegram",
        action="send",
        text="default channel",
        # no target → defaults to TraceContext channel
    )
    assert result.success is True
    assert deliverer.calls[0].channel_name == "telegram"
    assert _decode(result.output)["target"] == "telegram"


async def test_cross_channel_target_honored() -> None:
    """Session is on telegram but the agent targets cli explicitly."""
    deliverer = _FakeDeliverer()
    await _run(
        SendMessageTool(),
        deliverer=deliverer,
        channel="telegram",
        action="send",
        text="cross channel",
        target="cli",
    )
    assert deliverer.calls[0].channel_name == "cli"


async def test_unknown_channel_structured_error_no_deliver() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        action="send",
        text="to nowhere",
        target="discord",  # not registered
    )
    assert result.success is False
    assert "unknown channel" in (result.error or "")
    assert deliverer.calls == []  # no deliver, no raise


async def test_no_target_no_session_channel_structured_error() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        channel=None,  # no session channel to default to
        action="send",
        text="orphan",
    )
    assert result.success is False
    assert "no target channel" in (result.error or "")
    assert deliverer.calls == []


async def test_blank_text_structured_error() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        action="send",
        text="   ",  # whitespace-only → blank after strip
        target="telegram",
    )
    assert result.success is False
    assert "blank text" in (result.error or "")
    assert deliverer.calls == []


async def test_list_returns_channel_names() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(SendMessageTool(), deliverer=deliverer, action="list")
    assert result.success is True
    record = _decode(result.output)
    assert record["action"] == "list"
    assert set(record["channels"]) == {"telegram", "cli"}
    assert deliverer.calls == []  # list never sends


async def test_flood_cap_rejects_over_limit() -> None:
    """The 3rd send in the window is rejected by the per-session flood cap."""
    deliverer = _FakeDeliverer()
    tool = SendMessageTool(flood_max=2, flood_window_seconds=60)
    ok1 = await _run(tool, deliverer=deliverer, action="send", text="one", target="cli")
    ok2 = await _run(tool, deliverer=deliverer, action="send", text="two", target="cli")
    rejected = await _run(
        tool, deliverer=deliverer, action="send", text="three", target="cli"
    )
    assert ok1.success is True
    assert ok2.success is True
    assert rejected.success is False
    assert "rate limited" in (rejected.error or "")
    assert len(deliverer.calls) == 2  # only the first two delivered


async def test_flood_cap_no_session_varying_target_still_caps() -> None:
    """MAJOR-2 regression: with no session_id, varying the target must NOT mint a
    fresh bucket per channel — all no-session sends share one process-wide bucket."""
    deliverer = _FakeDeliverer()
    tool = SendMessageTool(flood_max=1, flood_window_seconds=60)
    ok = await _run(tool, deliverer=deliverer, session_id=None,
                    action="send", text="one", target="cli")
    # Different target, still no session → SAME bucket → rejected (target can't evade).
    rejected = await _run(tool, deliverer=deliverer, session_id=None,
                          action="send", text="two", target="telegram")
    assert ok.success is True
    assert rejected.success is False
    assert "rate limited" in (rejected.error or "")
    assert len(deliverer.calls) == 1  # target-vary did not evade the cap


async def test_flood_cap_default_eleventh_rejected() -> None:
    """With the default cap (10/60s) the 11th send in the window is rejected."""
    deliverer = _FakeDeliverer()
    tool = SendMessageTool()
    for _ in range(10):
        r = await _run(tool, deliverer=deliverer, action="send", text="x", target="cli")
        assert r.success is True
    eleventh = await _run(
        tool, deliverer=deliverer, action="send", text="x", target="cli"
    )
    assert eleventh.success is False
    assert "rate limited" in (eleventh.error or "")
    assert len(deliverer.calls) == 10


async def test_deliverer_none_structured_deferred_no_raise() -> None:
    result = await _run(
        SendMessageTool(),
        deliverer=None,
        action="send",
        text="deliverer is down",
        target="telegram",
    )
    assert result.success is True  # structured, not a raise
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_deliver_failed_structured_no_raise() -> None:
    deliverer = _FakeDeliverer(status="failed")
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        action="send",
        text="transport will fail",
        target="telegram",
    )
    assert result.success is True
    assert _decode(result.output)["delivery_status"] == "failed"


async def test_deliver_raises_self_heals_to_deferred() -> None:
    class _Raiser:
        async def deliver(self, notification: Notification) -> str:
            raise RuntimeError("boom")

    result = await _run(
        SendMessageTool(),
        deliverer=_Raiser(),
        action="send",
        text="deliver throws",
        target="telegram",
    )
    assert result.success is True  # never raises out of execute
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_urgency_is_normal_agent_cannot_send_critical() -> None:
    """Agent sends are HARD-clamped to normal — there is no path to critical."""
    deliverer = _FakeDeliverer()
    await _run(
        SendMessageTool(),
        deliverer=deliverer,
        action="send",
        text="not critical",
        target="telegram",
    )
    assert deliverer.calls[0].urgency == "normal"


async def test_unknown_action_structured_error() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(SendMessageTool(), deliverer=deliverer, action="broadcast")
    assert result.success is False
    assert "Unknown action" in (result.error or "")
    assert deliverer.calls == []


async def test_extra_field_forbidden() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        action="send",
        text="hi",
        target="telegram",
        bogus="nope",
    )
    assert result.success is False
    assert deliverer.calls == []
