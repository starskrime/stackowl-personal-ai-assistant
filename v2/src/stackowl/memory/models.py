"""Pydantic models for the memory knowledge pipeline (StagedFact, MemoryRecord)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StagedFact(BaseModel):
    """A fact extracted from a source and queued for promotion to long-term memory.

    Staged facts live in ``staged_facts`` until they accrue enough reinforcement
    or pass the confidence threshold, at which point they are promoted into
    ``committed_facts`` and rebuilt into the FTS5 index.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source_type: Literal["conversation", "parliament", "manual"]
    source_ref: str
    confidence: float = Field(ge=0.0, le=1.0)
    staged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reinforcement_count: int = 0
    status: Literal["staged", "committed", "rejected"] = "staged"
    embedding: list[float] | None = None
    embedding_model: str | None = None


class MemoryRecord(BaseModel):
    """A committed (long-term) fact, returned by :meth:`MemoryBridge.recall`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    content: str
    embedding: list[float]
    embedding_model: str
    committed_at: datetime
    source_type: str
    source_ref: str
    tags: list[str] = Field(default_factory=list)
