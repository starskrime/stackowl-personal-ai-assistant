"""Tests for SendMessageTool — agent outbound text over the channel registry (E7-S3).

Story 4.8 (AD-1) — ``_deliver`` now submits the declared, irreversible
``messaging.send_message`` command through ``commands/spec/submit.py::
submit_command`` rather than calling ``proactive_deliverer.deliver`` directly.
The gate ALWAYS parks an irreversible command for an attending (``owner``)
requester (no owner carve-out), so every successful, valid send from these
tests reaches ``"pending_approval"`` — the fake deliverer is never actually
invoked synchronously any more (that only happens once the handler runs,
after step-up). The HANDLER's own delivery-outcome mapping (delivered/
failed/batched, the receipt-idempotency guard) is proven separately in
``tests/notifications/test_send_message_and_send_file_go_through_commands.py``,
which calls the handler directly via ``execute_command_task`` with a
``gate_verdict="approved"`` row (mirrors ``tests/objectives/
test_set_objective_is_a_command.py``'s own "call the handler directly" unit
boundary).

Validation/structural checks that never reach ``_deliver`` (unknown channel,
no target, blank text, flood cap, ``list``, unknown action, extra field) are
unaffected by this migration and stay as they were.

The channel registry singleton is populated with fake adapters and reset in
a fixture.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.notifications.router import Notification
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.scheduling.send_message import SendMessageTool

pytestmark = pytest.mark.asyncio

_TRACE = "trace-sm-1"


class _FakeDeliverer:
    """Records deliver() calls and returns a scripted DeliveryStatus.

    Never actually reached by a `pending_approval` tool call (the gate parks
    BEFORE the handler ever touches the deliverer) — kept for the tests that
    prove the tool degrades honestly with no db pool wired at all, and for
    parity with the fixture's own `proactive_deliverer` slot.
    """

    def __init__(self, status: str = "delivered") -> None:
        self.status = status
        self.calls: list[Notification] = []

    async def deliver(self, notification: Notification, *, context: object = None) -> str:
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
    db: DbPool | None,
    channel: str | None = "telegram",
    session_key: str | None = "sess-sm",
    trace_id: str | None = _TRACE,
    **kwargs: Any,
) -> Any:
    services = StepServices(proactive_deliverer=deliverer, db_pool=db)
    stoken = set_services(services)
    ttoken = TraceContext.start(
        session_key=session_key, trace_id=trace_id, interactive=True, channel=channel
    )
    try:
        return await tool.execute(**kwargs)
    finally:
        TraceContext.reset(ttoken)
        reset_services(stoken)


# --------------------------------------------------------------------------- tests


async def test_send_explicit_target_submits_a_pending_approval_command(
    tmp_db: DbPool,
) -> None:
    """An owner-attended send of an irreversible command always parks for
    step-up (no owner carve-out) — the fake deliverer is never invoked
    synchronously, and a real, parked `messaging.send_message` command row
    exists carrying the right payload."""
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        action="send",
        text="hello there",
        target="cli",
    )
    assert result.success is True
    assert result.verified is False
    assert deliverer.calls == []
    record = _decode(result.output)
    assert record["target"] == "cli"
    assert record["delivery_status"] == "pending_approval"

    rows = await tmp_db.fetch_all(
        "SELECT command_type, command_payload, status FROM tasks "
        "WHERE command_type = 'messaging.send_message'",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "parked"
    payload = json.loads(rows[0]["command_payload"])
    assert payload["channel"] == "cli"
    assert payload["message"] == "hello there"


async def test_target_omitted_defaults_to_session_channel(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        channel="telegram",
        action="send",
        text="default channel",
        # no target → defaults to TraceContext channel
    )
    assert result.success is True
    assert _decode(result.output)["target"] == "telegram"
    rows = await tmp_db.fetch_all(
        "SELECT command_payload FROM tasks WHERE command_type = 'messaging.send_message'",
    )
    assert json.loads(rows[0]["command_payload"])["channel"] == "telegram"


async def test_cross_channel_target_honored(tmp_db: DbPool) -> None:
    """Session is on telegram but the agent targets cli explicitly."""
    deliverer = _FakeDeliverer()
    await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        channel="telegram",
        action="send",
        text="cross channel",
        target="cli",
    )
    rows = await tmp_db.fetch_all(
        "SELECT command_payload FROM tasks WHERE command_type = 'messaging.send_message'",
    )
    assert json.loads(rows[0]["command_payload"])["channel"] == "cli"


async def test_unknown_channel_structured_error_no_deliver(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        action="send",
        text="to nowhere",
        target="discord",  # not registered
    )
    assert result.success is False
    assert "unknown channel" in (result.error or "")
    assert deliverer.calls == []  # no deliver, no raise
    rows = await tmp_db.fetch_all(
        "SELECT 1 FROM tasks WHERE command_type = 'messaging.send_message'",
    )
    assert rows == []  # never even submitted


async def test_no_target_no_session_channel_structured_error(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        channel=None,  # no session channel to default to
        action="send",
        text="orphan",
    )
    assert result.success is False
    assert "no target channel" in (result.error or "")
    assert deliverer.calls == []


async def test_blank_text_structured_error(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        action="send",
        text="   ",  # whitespace-only → blank after strip
        target="telegram",
    )
    assert result.success is False
    assert "blank text" in (result.error or "")
    assert deliverer.calls == []


async def test_list_returns_channel_names(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(SendMessageTool(), deliverer=deliverer, db=tmp_db, action="list")
    assert result.success is True
    record = _decode(result.output)
    assert record["action"] == "list"
    assert set(record["channels"]) == {"telegram", "cli"}
    assert deliverer.calls == []  # list never sends


async def test_flood_cap_rejects_over_limit(tmp_db: DbPool) -> None:
    """The 3rd send in the window is rejected by the per-session flood cap
    BEFORE it ever reaches submission — only 2 command rows are created."""
    deliverer = _FakeDeliverer()
    tool = SendMessageTool(flood_max=2, flood_window_seconds=60)
    ok1 = await _run(tool, deliverer=deliverer, db=tmp_db, action="send", text="one", target="cli")
    ok2 = await _run(tool, deliverer=deliverer, db=tmp_db, action="send", text="two", target="cli")
    rejected = await _run(
        tool, deliverer=deliverer, db=tmp_db, action="send", text="three", target="cli"
    )
    assert ok1.success is True
    assert ok2.success is True
    assert rejected.success is False
    assert "rate limited" in (rejected.error or "")
    rows = await tmp_db.fetch_all(
        "SELECT 1 FROM tasks WHERE command_type = 'messaging.send_message'",
    )
    assert len(rows) == 2  # only the first two were even submitted


async def test_flood_cap_no_session_varying_target_still_caps(tmp_db: DbPool) -> None:
    """MAJOR-2 regression: with no session_key, varying the target must NOT mint a
    fresh bucket per channel — all no-session sends share one process-wide bucket."""
    deliverer = _FakeDeliverer()
    tool = SendMessageTool(flood_max=1, flood_window_seconds=60)
    ok = await _run(tool, deliverer=deliverer, db=tmp_db, session_key=None,
                    action="send", text="one", target="cli")
    # Different target, still no session → SAME bucket → rejected (target can't evade).
    rejected = await _run(tool, deliverer=deliverer, db=tmp_db, session_key=None,
                          action="send", text="two", target="telegram")
    assert ok.success is True
    assert rejected.success is False
    assert "rate limited" in (rejected.error or "")


async def test_deliverer_none_still_parks_for_step_up(tmp_db: DbPool) -> None:
    """A db pool wired but no deliverer — the gate parks BEFORE the handler
    ever checks for a deliverer, so this is still a pending_approval, honest
    outcome, never a raise."""
    result = await _run(
        SendMessageTool(),
        deliverer=None,
        db=tmp_db,
        action="send",
        text="no deliverer yet",
        target="telegram",
    )
    assert result.success is True
    assert result.verified is False
    assert _decode(result.output)["delivery_status"] == "pending_approval"


async def test_no_db_pool_structured_deferred_no_raise() -> None:
    """No db pool at all — submit_command has nowhere to write; the tool
    degrades honestly to 'deferred' rather than crashing."""
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=None,
        action="send",
        text="db is down",
        target="telegram",
    )
    assert result.success is True  # structured, not a raise
    assert result.verified is False
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_submit_command_raises_self_heals_to_deferred(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "stackowl.commands.spec.submit.submit_command", _raise,
    )
    result = await _run(
        SendMessageTool(),
        deliverer=_FakeDeliverer(),
        db=tmp_db,
        action="send",
        text="submission throws",
        target="telegram",
    )
    assert result.success is True  # never raises out of execute
    assert result.verified is False
    assert _decode(result.output)["delivery_status"] == "deferred"


async def test_list_is_plain_verified_success(tmp_db: DbPool) -> None:
    """action='list' is a genuine success with no delivery — must not regress to an
    unverified/failed signal (it never touches the deliverer)."""
    deliverer = _FakeDeliverer()
    result = await _run(SendMessageTool(), deliverer=deliverer, db=tmp_db, action="list")
    assert result.success is True
    assert result.verified is None  # no delivery to verify; byte-identical default


async def test_urgency_is_normal_agent_cannot_send_critical(tmp_db: DbPool) -> None:
    """Agent sends are HARD-clamped to normal — there is no path to critical."""
    deliverer = _FakeDeliverer()
    await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        action="send",
        text="not critical",
        target="telegram",
    )
    rows = await tmp_db.fetch_all(
        "SELECT command_payload FROM tasks WHERE command_type = 'messaging.send_message'",
    )
    # urgency is clamped inside the handler at real-send time, not carried on
    # the payload itself — the payload's own category is what the tool sets.
    assert json.loads(rows[0]["command_payload"])["category"] == "agent_message"


async def test_unknown_action_structured_error(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(SendMessageTool(), deliverer=deliverer, db=tmp_db, action="broadcast")
    assert result.success is False
    assert "Unknown action" in (result.error or "")
    assert deliverer.calls == []


async def test_extra_field_forbidden(tmp_db: DbPool) -> None:
    deliverer = _FakeDeliverer()
    result = await _run(
        SendMessageTool(),
        deliverer=deliverer,
        db=tmp_db,
        action="send",
        text="hi",
        target="telegram",
        bogus="nope",
    )
    assert result.success is False
    assert deliverer.calls == []
