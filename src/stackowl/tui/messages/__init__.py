"""TUI message types — frozen dataclasses bridging EventBus events to Textual."""

from __future__ import annotations

from stackowl.tui.messages.compose import (
    AutocompleteSelectedMessage,
    ComposeSubmittedMessage,
)
from stackowl.tui.messages.conversation import (
    FactCitation,
    ResponseChunkMessage,
    UserTurnMessage,
)
from stackowl.tui.messages.jobs import BudgetAlertMessage, JobPausedMessage
from stackowl.tui.messages.layout import (
    ComposeAreaStateMessage,
    LayoutTierChangedMessage,
)
from stackowl.tui.messages.overlay import (
    OpenEvolutionInspectionMessage,
    OverlayClosedMessage,
    ToastRequestMessage,
)
from stackowl.tui.messages.parliament import (
    EvolutionBadgeMessage,
    MemoryFactMessage,
    ParliamentClosedMessage,
    ParliamentRoundMessage,
    ParliamentRoundStartedMessage,
    ParliamentStartedMessage,
    SynthesisArrivedMessage,
)
from stackowl.tui.messages.pipeline import (
    CostUpdatedMessage,
    DegradedProviderMessage,
    PipelineStepMessage,
    ProviderChangedMessage,
)

__all__ = [
    "AutocompleteSelectedMessage",
    "BudgetAlertMessage",
    "ComposeAreaStateMessage",
    "ComposeSubmittedMessage",
    "CostUpdatedMessage",
    "DegradedProviderMessage",
    "EvolutionBadgeMessage",
    "FactCitation",
    "JobPausedMessage",
    "LayoutTierChangedMessage",
    "MemoryFactMessage",
    "OpenEvolutionInspectionMessage",
    "OverlayClosedMessage",
    "ParliamentClosedMessage",
    "ParliamentRoundMessage",
    "ParliamentRoundStartedMessage",
    "ParliamentStartedMessage",
    "PipelineStepMessage",
    "ProviderChangedMessage",
    "ResponseChunkMessage",
    "SynthesisArrivedMessage",
    "ToastRequestMessage",
    "UserTurnMessage",
]
