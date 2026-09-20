"""F-76 — ``/urgent`` reports success ONLY when the real transport seam delivered.

Before the fix, ``UrgentCommand`` called ``NotificationRouter.deliver`` (a pure
routing DECISION that "never touches a channel adapter") and counted the absence
of an exception as a "broadcast to N channels". Nothing actually reached a user,
yet the command claimed delivery — an overclaim.

The fix routes ``/urgent`` through the ``ProactiveDeliverer`` (the transport seam
that calls ``send_text`` and returns a real :data:`DeliveryStatus`), and derives
the user-facing count from the ACTUAL ``delivered`` outcomes, not from the lack of
an exception. A channel whose transport ``failed`` must NOT be counted as
delivered.

Story 4.8 (AD-1) — ``/urgent`` now SUBMITS the declared, irreversible
``notifications.broadcast_urgent`` command instead of calling
``self._deliverer.deliver`` directly; the per-channel delivered/failed
counting this file pins now happens inside ``notifications/commands.py``'s
handler. These tests stub ``commands/spec/submit.py::submit_command``
(``stub_submit_command``) to script that handler's own reported counts, and
assert ``UrgentCommand``'s own text-formatting reads them honestly — the
handler's OWN counting logic is proven directly in
``tests/notifications/test_urgent_and_brief_go_through_commands.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.urgent_command import UrgentCommand
from stackowl.pipeline.services import StepServices, reset_services, set_services
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401
from tests._story_7_2_helpers import stub_submit_command

pytestmark = pytest.mark.asyncio


async def _handle_with_db(cmd: UrgentCommand, message: str) -> str:
    """Story 4.8 — ``UrgentCommand`` now needs ``get_services().db_pool`` to
    even attempt ``submit_command``; a plain sentinel is enough once
    ``submit_command`` itself is stubbed."""
    token = set_services(StepServices(db_pool=object()))  # type: ignore[arg-type]
    try:
        return await cmd.handle(message, make_state())
    finally:
        reset_services(token)


def _make_channel_adapter(name: str) -> MagicMock:
    adapter = MagicMock()
    adapter.channel_name = name
    return adapter


@pytest.fixture(autouse=True)
def _reset_registries() -> None:
    from stackowl.commands.registry import CommandRegistry

    CommandRegistry.reset()
    ChannelRegistry.instance().reset()


async def test_urgent_delivers_via_real_transport_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """All channels transport ``delivered`` → count reflects real deliveries."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))
    ChannelRegistry.instance().register(_make_channel_adapter("telegram"))

    calls = stub_submit_command(
        monkeypatch, result={"delivered": 2, "failed": 0, "total": 2}, success=True,
    )
    deliverer = MagicMock()
    deliverer.deliver = AsyncMock()
    cmd = UrgentCommand(deliverer=deliverer)

    result = await _handle_with_db(cmd, "system alert")

    # The command was actually submitted — NOT a bare routing decision.
    assert len(calls) == 1
    assert calls[0]["command_type"] == "notifications.broadcast_urgent"
    assert set(calls[0]["payload"].channels) == {"cli", "telegram"}
    assert "2" in result
    assert "delivered" in result.lower()


async def test_urgent_failed_transport_not_counted_as_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel whose transport ``failed`` must NOT inflate the delivered count."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))
    ChannelRegistry.instance().register(_make_channel_adapter("telegram"))

    stub_submit_command(monkeypatch, result={"delivered": 1, "failed": 1, "total": 2}, success=True)
    deliverer = MagicMock()
    deliverer.deliver = AsyncMock()
    cmd = UrgentCommand(deliverer=deliverer)

    result = await _handle_with_db(cmd, "system alert")

    # Honest: 1 of 2 delivered — must NOT claim "delivered to 2".
    assert "1" in result
    assert "2 channels" not in result.replace("/2", "")  # no "delivered to 2 channels"
    # Failure must be surfaced, not hidden.
    assert "fail" in result.lower() or "/2" in result


async def test_urgent_all_failed_reports_zero_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every channel failing transport reports zero delivered — never a fake win.

    The handler's own `success = delivered > 0 or total == 0` means an
    all-failed broadcast reports `outcome.success=False`, and
    UrgentCommand honestly surfaces that as a failure — never a fake win.
    """
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))

    stub_submit_command(monkeypatch, result={"delivered": 0, "failed": 1, "total": 1}, success=False)
    deliverer = MagicMock()
    deliverer.deliver = AsyncMock()
    cmd = UrgentCommand(deliverer=deliverer)

    result = await _handle_with_db(cmd, "system alert")

    # Must not claim a broadcast/delivery happened.
    assert "delivered to 1" not in result.lower()
    assert "failed" in result.lower()


async def test_urgent_owner_attended_pending_approval_is_honestly_reported(
    tmp_db: object,
) -> None:
    """No stub — the REAL gate parks an owner-attended irreversible broadcast
    (no owner carve-out); the reply must say so, never claim a delivery."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))
    deliverer = MagicMock()
    deliverer.deliver = AsyncMock()
    cmd = UrgentCommand(deliverer=deliverer, channels=["cli"])
    token = set_services(StepServices(db_pool=tmp_db, proactive_deliverer=deliverer))  # type: ignore[arg-type]
    try:
        result = await cmd.handle("system alert", make_state())
    finally:
        reset_services(token)

    assert "awaiting your approval" in result
    deliverer.deliver.assert_not_called()
