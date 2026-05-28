"""Lesson — unified shape for any artifact entering the lessons index.

A Lesson is the cross-source learning record:
* ``source_type`` — which subsystem produced it (reflection/skill/etc.)
* ``source_ref`` — opaque pointer back to the canonical row in that subsystem
* ``content`` — text fed to the embedder + shown to LLMs at retrieval
* ``metadata`` — sidecar JSON for query-time filters (tool_name, owl_name,
  failure_class, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LessonSource = Literal[
    "reflection",
    "skill",
    "tool_heuristic",
    "knowledge_pellet",
]


@dataclass(frozen=True)
class Lesson:
    """Canonical pre-publish shape for a learning artifact."""

    lesson_id: str                # globally unique; convention "<source>:<source_ref>"
    source_type: LessonSource
    source_ref: str               # foreign-key back to the canonical row
    content: str                  # what the embedder + LLM both consume
    embedding: list[float]        # required at publish time (caller embeds)
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class LessonHit:
    """Search-side projection — what callers see from LessonsIndex.search()."""

    lesson_id: str
    source_type: LessonSource
    source_ref: str
    content: str
    similarity: float             # 1/(1+distance) in [0, 1]
    metadata: dict[str, object]


def make_lesson_id(source_type: LessonSource, source_ref: str) -> str:
    """Stable id convention used everywhere lessons are published."""
    return f"{source_type}:{source_ref}"
