"""Conversation streaming messages — chunks and citation references."""

from __future__ import annotations

from dataclasses import dataclass, field

from stackowl.tui.messages._base import FrozenMessage


@dataclass
class FactCitation:
    """A citation reference embedded in a response chunk.

    Plain (mutable) dataclass: callers build a tuple of citations and freeze it
    on the enclosing :class:`ResponseChunkMessage`.
    """

    fact_id: str
    snippet: str
    index: int


@dataclass(frozen=True)
class ResponseChunkMessage(FrozenMessage):
    """Carries a single streaming response chunk to :class:`ConversationView`.

    Attributes:
        text: The token fragment to append to the active transcript line.
        owl_name: Authoring owl identifier (used for styling / attribution).
        citations: Tuple of :class:`FactCitation` references found in this chunk.
            A tuple (not a list) is required so the dataclass remains hashable.
        is_pushback: ``True`` when the chunk is a parliament pushback turn.
        is_synthesis: ``True`` when the chunk is the final synthesis turn.
        chunk_index: Monotonic chunk index from the underlying stream.
        trace_id: W3C-style trace identifier propagated from the request.
        is_final: ``True`` on the last chunk of a turn — closes the active
            bubble so the next chunk opens a fresh one.
    """

    text: str
    owl_name: str
    citations: tuple[FactCitation, ...] = field(default_factory=tuple)
    is_pushback: bool = False
    is_synthesis: bool = False
    chunk_index: int = 0
    trace_id: str = ""
    is_final: bool = False


@dataclass(frozen=True)
class UserTurnMessage(FrozenMessage):
    """Carries the user's own submitted turn to :class:`ConversationView`.

    Previously the user's input was published straight to the engine and never
    echoed back to the transcript.  This message lets the app render the user's
    turn locally so the conversation reads like a chat.

    Attributes:
        text: The raw text the user submitted (rendered, never markup-parsed).
    """

    text: str
