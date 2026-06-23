"""LocalTurnClient — channel-keyed dispatch of the shared ingress body."""

from __future__ import annotations

import pytest

from stackowl.gateway.scanner import IngressMessage
from stackowl.runtime.turn_client import LocalTurnClient, UnregisteredChannelError


def _msg(channel: str) -> IngressMessage:
    return IngressMessage(text="hi", session_id="s1", channel=channel, trace_id="t1")


async def test_submit_routes_to_registered_channel() -> None:
    calls: list[tuple] = []

    async def handler(pump, adapter, msg):
        calls.append((pump, adapter, msg))

    client = LocalTurnClient(handler)
    client.register_channel("cli", pump="cli-pump", adapter="cli-adapter")
    msg = _msg("cli")
    await client.submit(msg)

    assert calls == [("cli-pump", "cli-adapter", msg)]


async def test_submit_routes_each_channel_to_its_own_binding() -> None:
    seen: list[tuple] = []

    async def handler(pump, adapter, msg):
        seen.append((pump, adapter, msg.channel))

    client = LocalTurnClient(handler)
    client.register_channel("cli", "cli-pump", "cli-adapter")
    client.register_channel("telegram", "tg-pump", "tg-adapter")

    await client.submit(_msg("telegram"))
    await client.submit(_msg("cli"))

    assert seen == [
        ("tg-pump", "tg-adapter", "telegram"),
        ("cli-pump", "cli-adapter", "cli"),
    ]


async def test_unregistered_channel_raises() -> None:
    async def handler(pump, adapter, msg):  # pragma: no cover — never called
        raise AssertionError("handler should not run")

    client = LocalTurnClient(handler)
    with pytest.raises(UnregisteredChannelError):
        await client.submit(_msg("nope"))


async def test_re_registering_a_channel_replaces_binding() -> None:
    seen: list = []

    async def handler(pump, adapter, msg):
        seen.append(pump)

    client = LocalTurnClient(handler)
    client.register_channel("cli", "old", "a")
    client.register_channel("cli", "new", "a")
    await client.submit(_msg("cli"))
    assert seen == ["new"]
