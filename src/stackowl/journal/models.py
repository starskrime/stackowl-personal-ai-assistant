"""The typed envelope and the base every event type's ``attrs`` model subclasses.

Mirrors ``ipc/frames.py``'s ``_Frame`` (frozen, ``extra="forbid"``) -- the same
"reject a field nobody declared, never silently drop it" contract the wire
protocol already relies on, applied to the journal's own metadata-only payloads
(AD-4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackowl.journal.enums import ActorKind, Outcome


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class JournalAttrsBase(BaseModel):
    """Base for every event type's typed, metadata-only ``attrs`` payload.

    AD-4: "``attrs`` hold ids, numbers, closed enums and bounded labels of at
    most 64 characters" -- never message text, memory content, prompts,
    secrets, display names or exception text. Frozen + ``extra="forbid"`` so a
    field nobody declared on the model fails construction rather than being
    silently accepted and later silently dropped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class RecordRef(BaseModel):
    """Where the row this event describes actually lives (AD-4).

    ``kind="sqlite"`` locators carry the owning table and row id; ``"md"`` a
    ``StackowlHome``-relative path and section anchor; ``"graph"`` a node id.
    Registering a reader per kind is explicitly OUT of Story 2.1's scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["sqlite", "md", "graph"]
    locator: dict[str, str]


class JournalEvent(BaseModel):
    """One recordable occurrence -- every envelope field ``record()`` needs
    except ``cursor``/``event_id``, which it fills.

    ``occurred_at`` defaults to now at construction time rather than at
    ``record()`` time -- close enough for Story 2.1 (no queueing between an
    emitter building this and calling ``record()`` in the same transaction
    block) and lets a test pin a value for determinism.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    schema_version: int
    occurred_at: str = Field(default_factory=_now_iso)
    actor_kind: ActorKind
    actor_id: str
    device_id: str | None = None
    #: Same closed list as ``actor_kind`` -- AD-3: "one closed list", singular.
    target_kind: ActorKind
    target_id: str
    outcome: Outcome
    attention: str | None = None
    intensity: str | None = None
    record_ref: RecordRef | None = None
    attrs: JournalAttrsBase
    trace_id: str | None = None
    duration_ms: int | None = None
