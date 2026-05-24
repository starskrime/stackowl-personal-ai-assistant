"""Parliament / memory / evolution messages."""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.tui.messages._base import FrozenMessage


@dataclass(frozen=True)
class ParliamentRoundMessage(FrozenMessage):
    """Emitted when a parliament round completes with per-owl responses."""

    session_id: str
    round_number: int
    owl_responses: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryFactMessage(FrozenMessage):
    """Emitted when a memory fact is added or updated."""

    fact_id: str
    content_preview: str


@dataclass(frozen=True)
class EvolutionBadgeMessage(FrozenMessage):
    """Emitted when an owl finishes an evolution batch with trait deltas."""

    owl_name: str
    changed_traits: dict[str, tuple[object, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class ParliamentStartedMessage(FrozenMessage):
    """Emitted when a Parliament session begins — drives roll-call display."""

    session_id: str
    owl_names: tuple[str, ...] = ()
    trigger: str = "explicit"


@dataclass(frozen=True)
class ParliamentRoundStartedMessage(FrozenMessage):
    """Emitted at the start of each Parliament round (before any responses)."""

    session_id: str
    round_number: int = 1


@dataclass(frozen=True)
class SynthesisArrivedMessage(FrozenMessage):
    """Emitted when ParliamentSynthesizer publishes a final synthesis result."""

    session_id: str
    consensus: str = ""
    recommendation: str = ""
    confidence: float = 0.0
    disagreements: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParliamentClosedMessage(FrozenMessage):
    """Emitted when the Parliament session has fully wrapped — hide panel."""

    session_id: str
