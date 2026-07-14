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
    source_type: Literal[
        "conversation", "conversation_fact", "parliament", "manual", "webpage", "screenshot", "agent_self",
    ]
    source_ref: str
    confidence: float = Field(ge=0.0, le=1.0)
    staged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reinforcement_count: int = 0
    status: Literal["staged", "committed", "rejected"] = "staged"
    embedding: list[float] | None = None
    embedding_model: str | None = None
    # Provenance trust tier (Story E). Default 'untrusted' = fail-safe (a forgotten stamp recalls fenced).
    trust: Literal["trusted", "self", "untrusted"] = "untrusted"
    # Phase 2 (coding-capability build plan) — optional scope (e.g. a repo path
    # or remote) distinct from user/conversation memory. None = global/unscoped,
    # byte-identical to every pre-Phase-2 fact.
    scope_key: str | None = None


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
    # Provenance trust tier (Story E). Default 'untrusted' = fail-safe (a forgotten stamp recalls fenced).
    trust: Literal["trusted", "self", "untrusted"] = "untrusted"
    # MEM-1 (F073) — reinforcement count carried from staging, so blended recall
    # can lift a repeatedly-confirmed preference over a stale one-off. Default 0
    # = a one-off (legacy rows backfill to 0 via the 0062 migration default).
    reinforcement_count: int = 0
    # Phase 2 (coding-capability build plan) — see StagedFact.scope_key.
    scope_key: str | None = None
