"""Pipeline / provider / cost messages."""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.tui.messages._base import FrozenMessage


@dataclass(frozen=True)
class PipelineStepMessage(FrozenMessage):
    """Emitted when the pipeline advances to a new step."""

    step_name: str
    step_index: int
    total_steps: int


@dataclass(frozen=True)
class ProviderChangedMessage(FrozenMessage):
    """Emitted when the active provider/tier selection changes."""

    provider_name: str
    tier: str


@dataclass(frozen=True)
class CostUpdatedMessage(FrozenMessage):
    """Emitted when the running daily cost figure updates."""

    cost_today: float


@dataclass(frozen=True)
class DegradedProviderMessage(FrozenMessage):
    """Emitted when a provider is forced into a degraded tier."""

    provider_name: str
    tier: str
    reason: str
