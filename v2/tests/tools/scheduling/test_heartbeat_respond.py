"""Tests for HeartbeatRespondTool — heartbeat outcome + clamped notify (E7-S2).

A fake ProactiveDeliverer records each ``deliver(Notification)`` call so the
tests can assert the urgency was clamped, the TraceContext channel was used, and
delivery happened exactly once. ``execute`` is called directly (the tool does no
TestModeGuard gating of its own). The once-per-turn guard is exercised by binding
the SAME ``trace_id`` across two calls via ``TraceContext.start``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.notifications.router import Notification
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.scheduling.heartbeat_respond import HeartbeatRespondTool

pytestmark = pytest.mark.asyncio

_TRACE = "trace-hb-1"


class _FakeDeliverer:
    """Records deliver() calls and returns a scripted DeliveryStatus."""

    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[Notification] = []

    async def deliver(self, notification: Notification) -> str:
        self.calls.append(notification)
        return self.status


def _decode(output: str) -> dict[str, Any]:
    return json.loads(output)["record"]


# --------------------------------------------------------------------------- helpers


async def _run(
    tool: HeartbeatRespondTool,
    *,
    deliverer: Any,
    channel: str | None = "telegram",
    trace_id: str | None = _TRACE,
    **kwargs: Any,
) -> Any:
    services = StepServices(proactive_deliverer=deliverer)
    stoken = set_services(services)
    ttoken = TraceContext.start(
        session_id="sess-hb", trace_id=trace_id, interactive=False, channel=channel
    )
    try:
        return await tool.execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(stoken)


# --------------------------------------------------------------------------- tests


async def test_valid_notify_false_records_no_deliver() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="nothing_urgent",
        notify=False,
        summary="All quiet on the build.",
    )
    assert result.success is True
    record = _decode(result.output)
    assert record["outcome"] == "nothing_urgent"
    assert record["notify"] is False
    assert record["delivery_status"] == "skipped"
    assert deliverer.calls == []  # no delivery when notify=False


async def test_notify_true_delivers_once_with_channel() -> None:
    deliverer = _FakeDeliverer(status="delivered")
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        channel="telegram",
        outcome="ci_failed",
        notify=True,
        summary="CI failed on main.",
        notification_text="Heads up: CI is red on main.",
    )
    assert result.success is True
    assert len(deliverer.calls) == 1  # delivered exactly once
    sent = deliverer.calls[0]
    assert sent.message == "Heads up: CI is red on main."
    assert sent.category == "heartbeat"
    assert sent.channel_name == "telegram"  # from TraceContext
    assert sent.urgency == "normal"
    record = _decode(result.output)
    assert record["delivery_status"] == "delivered"


async def test_priority_critical_clamped_to_normal_hard_gate() -> None:
    """HARD GATE proof: an agent asking for 'critical' is neutralized to 'normal'."""
    deliverer = _FakeDeliverer()
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="alert",
        notify=True,
        summary="Something happened.",
        priority="critical",
    )
    assert result.success is True
    assert len(deliverer.calls) == 1
    assert deliverer.calls[0].urgency == "normal"  # critical -> normal
    assert _decode(result.output)["priority"] == "normal"


async def test_priority_low_passes_through() -> None:
    deliverer = _FakeDeliverer()
    await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="fyi",
        notify=True,
        summary="Low priority note.",
        priority="low",
    )
    assert deliverer.calls[0].urgency == "low"


async def test_missing_required_field_validation_error() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="x",
        notify=True,
        # summary missing
    )
    assert result.success is False
    assert "invalid arguments" in (result.error or "")
    assert deliverer.calls == []


async def test_extra_field_forbidden() -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="x",
        notify=False,
        summary="s",
        bogus="nope",
    )
    assert result.success is False
    assert deliverer.calls == []


async def test_once_per_turn_second_call_blocked() -> None:
    """Same trace_id across two calls — the second is refused, no second deliver."""
    deliverer = _FakeDeliverer()
    tool = HeartbeatRespondTool()  # singleton instance carries the guard

    first = await _run(
        tool, deliverer=deliverer, trace_id=_TRACE,
        outcome="a", notify=True, summary="first",
    )
    second = await _run(
        tool, deliverer=deliverer, trace_id=_TRACE,
        outcome="b", notify=True, summary="second",
    )
    assert first.success is True
    assert second.success is True
    assert json.loads(second.output)["note"].startswith("already responded")
    assert len(deliverer.calls) == 1  # only the first delivered


async def test_distinct_trace_not_blocked() -> None:
    deliverer = _FakeDeliverer()
    tool = HeartbeatRespondTool()
    await _run(tool, deliverer=deliverer, trace_id="t-1",
               outcome="a", notify=True, summary="one")
    await _run(tool, deliverer=deliverer, trace_id="t-2",
               outcome="b", notify=True, summary="two")
    assert len(deliverer.calls) == 2  # different turns both deliver


async def test_failed_delivery_allows_retry_same_trace() -> None:
    """Self-healing: a failed/deferred send delivered nothing, so the SAME trace
    may retry within the turn — the guard must NOT have consumed the turn."""
    deliverer = _FakeDeliverer(status="failed")
    tool = HeartbeatRespondTool()
    first = await _run(tool, deliverer=deliverer, trace_id=_TRACE,
                       outcome="a", notify=True, summary="will fail")
    assert _decode(first.output)["delivery_status"] == "failed"
    # Now the deliverer recovers; the retry in the same trace must go through.
    deliverer.status = "delivered"
    second = await _run(tool, deliverer=deliverer, trace_id=_TRACE,
                        outcome="a", notify=True, summary="retry")
    assert "already responded" not in json.loads(second.output)["note"]
    assert len(deliverer.calls) == 2  # retry delivered, not blocked


async def test_none_trace_not_blocked() -> None:
    """Untraced turns (trace_id None) skip the guard rather than collapse to one bucket."""
    deliverer = _FakeDeliverer()
    tool = HeartbeatRespondTool()
    await _run(tool, deliverer=deliverer, trace_id=None,
               outcome="a", notify=True, summary="one")
    await _run(tool, deliverer=deliverer, trace_id=None,
               outcome="b", notify=True, summary="two")
    assert len(deliverer.calls) == 2  # both untraced turns deliver


async def test_empty_message_skipped_no_deliver() -> None:
    """notify=True but no message body (blank summary, no notification_text) → no blank push."""
    deliverer = _FakeDeliverer()
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="alert",
        notify=True,
        summary="   ",  # whitespace-only → empty after strip
    )
    assert result.success is True
    assert _decode(result.output)["delivery_status"] == "skipped"
    assert deliverer.calls == []  # nothing delivered


async def test_deliverer_none_structured_deferred_no_raise() -> None:
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=None,
        outcome="alert",
        notify=True,
        summary="deliverer is down",
    )
    assert result.success is True  # structured, not a raise
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_deliver_failed_structured_no_raise() -> None:
    deliverer = _FakeDeliverer(status="failed")
    result = await _run(
        HeartbeatRespondTool(),
        deliverer=deliverer,
        outcome="alert",
        notify=True,
        summary="transport will fail",
    )
    assert result.success is True
    assert _decode(result.output)["delivery_status"] == "failed"


async def test_deliver_raises_self_heals_to_deferred() -> None:
    class _Raiser:
        async def deliver(self, notification: Notification) -> str:
            raise RuntimeError("boom")

    result = await _run(
        HeartbeatRespondTool(),
        deliverer=_Raiser(),
        outcome="alert",
        notify=True,
        summary="deliver throws",
    )
    assert result.success is True  # belt-and-braces: never raises out of execute
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_guard_bounded_evicts_oldest() -> None:
    """The FIFO guard evicts the oldest trace once over the cap (bounded memory)."""
    deliverer = _FakeDeliverer()
    tool = HeartbeatRespondTool(guard_max=2)
    for tid in ("t-1", "t-2", "t-3"):  # t-1 evicted when t-3 added
        await _run(tool, deliverer=deliverer, trace_id=tid,
                   outcome="o", notify=False, summary="s")
    # t-1 was evicted, so a re-call on t-1 is treated as a fresh turn (not blocked)
    result = await _run(tool, deliverer=deliverer, trace_id="t-1",
                        outcome="o", notify=False, summary="s")
    assert "already responded" not in json.loads(result.output)["note"]
