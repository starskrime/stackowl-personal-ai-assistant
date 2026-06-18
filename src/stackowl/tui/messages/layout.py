"""Layout / compose-area state messages."""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.tui.messages._base import FrozenMessage


@dataclass(frozen=True)
class LayoutTierChangedMessage(FrozenMessage):
    """Emitted when the responsive layout tier changes.

    Attributes:
        tier: The new tier value (``"minimal"``, ``"compact"``, ``"standard"``,
            or ``"expanded"``) — string form so the message can be serialised
            without dragging the enum across the wire.
    """

    tier: str


@dataclass(frozen=True)
class ComposeAreaStateMessage(FrozenMessage):
    """Emitted when the compose area changes operating state.

    Attributes:
        state: One of ``"idle"``, ``"composing"``, ``"submitting"``,
            ``"mcp-disabled"`` — coordinates the input lock-down when an MCP
            spectator is connected.
    """

    state: str
