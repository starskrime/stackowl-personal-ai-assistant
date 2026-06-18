"""Overlay lifecycle messages (closed signal, inspection requests, toasts)."""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.tui.messages._base import FrozenMessage


@dataclass(frozen=True)
class OverlayClosedMessage(FrozenMessage):
    """Emitted by :class:`OverlayPanel.close` so the queue can advance."""

    overlay_name: str


@dataclass(frozen=True)
class OpenEvolutionInspectionMessage(FrozenMessage):
    """Request to surface the DNA-trait inspection panel for an owl."""

    owl_name: str
    changed_traits: dict[str, tuple[object, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class ToastRequestMessage(FrozenMessage):
    """Request to display a transient toast notification."""

    message: str
    urgency: str = "normal"
