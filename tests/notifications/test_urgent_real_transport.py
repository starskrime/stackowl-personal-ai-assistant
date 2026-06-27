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
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.channels.registry import ChannelRegistry
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.urgent_command import UrgentCommand
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401

pytestmark = pytest.mark.asyncio


def _make_deliverer(results: list[str]) -> MagicMock:
    """A ProactiveDeliverer stub whose ``deliver`` returns ``results`` in order."""
    deliverer = MagicMock()
    deliverer.deliver = AsyncMock(side_effect=results)
    return deliverer


def _make_channel_adapter(name: str) -> MagicMock:
    adapter = MagicMock()
    adapter.channel_name = name
    return adapter


@pytest.fixture(autouse=True)
def _reset_registries() -> None:
    CommandRegistry.reset()
    ChannelRegistry.instance().reset()


async def test_urgent_delivers_via_real_transport_seam() -> None:
    """All channels transport ``delivered`` → count reflects real deliveries."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))
    ChannelRegistry.instance().register(_make_channel_adapter("telegram"))

    deliverer = _make_deliverer(["delivered", "delivered"])
    cmd = UrgentCommand(deliverer=deliverer)

    result = await cmd.handle("system alert", make_state())

    # Transport seam was actually exercised — NOT a bare routing decision.
    assert deliverer.deliver.call_count == 2
    assert "2" in result
    # The verb must reflect real transport, not a routing decision.
    assert "delivered" in result.lower()


async def test_urgent_failed_transport_not_counted_as_delivered() -> None:
    """A channel whose transport ``failed`` must NOT inflate the delivered count."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))
    ChannelRegistry.instance().register(_make_channel_adapter("telegram"))

    # cli delivers, telegram fails transport.
    deliverer = _make_deliverer(["delivered", "failed"])
    cmd = UrgentCommand(deliverer=deliverer)

    result = await cmd.handle("system alert", make_state())

    assert deliverer.deliver.call_count == 2
    # Honest: 1 of 2 delivered — must NOT claim "delivered to 2".
    assert "1" in result
    assert "2 channels" not in result.replace("/2", "")  # no "delivered to 2 channels"
    # Failure must be surfaced, not hidden.
    assert "fail" in result.lower() or "/2" in result


async def test_urgent_all_failed_reports_zero_delivered() -> None:
    """Every channel failing transport reports zero delivered — never a fake win."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))

    deliverer = _make_deliverer(["failed"])
    cmd = UrgentCommand(deliverer=deliverer)

    result = await cmd.handle("system alert", make_state())

    assert deliverer.deliver.call_count == 1
    assert "0" in result
    # Must not claim a broadcast/delivery happened.
    assert "delivered to 1" not in result.lower()


async def test_urgent_wired_through_assembly_uses_deliverer() -> None:
    """register_all_commands threads the deliverer so the live path transports."""
    ChannelRegistry.instance().register(_make_channel_adapter("cli"))

    deliverer = _make_deliverer(["delivered"])
    router = MagicMock()
    router.deliver = AsyncMock(return_value="delivered")
    deps = CommandDeps(router=router, proactive_deliverer=deliverer)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "urgent", "system alert", make_state()
    )

    # The deliverer (real transport) must be used, NOT the bare router.
    assert deliverer.deliver.call_count == 1
    assert "1" in result
