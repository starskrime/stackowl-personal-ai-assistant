"""TurnClient — the gateway->core submission seam.

The five channel receive loops (CLI/Telegram/Slack/Discord/WhatsApp) share one
body: scan the raw message, resolve a reply into a parked clarify, and (if not
consumed) hand it to the non-blocking intake engine. That body is the boundary
between the GATEWAY (channels/adapters) and the CORE (scan/route/dispatch).

``TurnClient`` names that boundary as ``submit(msg)``. The in-process
``LocalTurnClient`` runs the body directly (byte-identical to the old inline
loop); the future ``SocketTurnClient`` will instead serialise the message to an
IngressFrame and let a core process run the body. Per-channel delivery resources
(the clarify pump + adapter) are gateway-side, so they are registered with the
local client by channel name and looked up by ``msg.channel`` — never threaded
through the interface (a socket core has no adapter handle).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.gateway.scanner import IngressMessage

# The shared loop body: (pump, adapter, msg) -> None. Typed loosely (object) to
# avoid importing the gateway-internal ClarifyPump / adapter types here.
IngressHandler = Callable[[object, object, "IngressMessage"], Awaitable[None]]


class TurnClient(Protocol):
    """The gateway-side handle for submitting an inbound message to the core."""

    async def submit(self, msg: IngressMessage) -> None:  # noqa: D102
        ...


class UnregisteredChannelError(KeyError):
    """A message arrived for a channel that was never registered with the client."""


class LocalTurnClient:
    """In-process TurnClient: dispatch the shared ingress body for ``msg.channel``.

    Behaviourally identical to the old inline loop body — it just routes through
    the per-channel (pump, adapter) pair registered at channel start-up.
    """

    def __init__(self, handler: IngressHandler) -> None:
        self._handler = handler
        self._channels: dict[str, tuple[object, object]] = {}

    def register_channel(self, channel_name: str, pump: object, adapter: object) -> None:
        """Bind a channel's clarify pump + adapter so ``submit`` can find them."""
        self._channels[channel_name] = (pump, adapter)

    async def submit(self, msg: IngressMessage) -> None:
        binding = self._channels.get(msg.channel)
        if binding is None:
            raise UnregisteredChannelError(msg.channel)
        pump, adapter = binding
        await self._handler(pump, adapter, msg)
