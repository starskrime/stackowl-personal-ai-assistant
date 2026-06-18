"""LessonsIndex — high-level publish + search over the LanceDB lessons table.

Subsystems publish into it (reflections after write, skills after embed,
tool heuristics after mining); subsystems also query it (tools after
execute, parliament before debate, classify per turn).

This module owns the EMBED-then-PUBLISH dance — callers pass a Lesson with
``embedding=[]`` or a raw ``LessonDraft`` and we embed before writing. Keeps
each subsystem from having to know about the EmbeddingRegistry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.learning.lesson import Lesson, LessonHit, LessonSource, make_lesson_id
from stackowl.learning.lessons_lance import LessonsLanceAdapter

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.embeddings.registry import EmbeddingRegistry


@dataclass(frozen=True)
class LessonDraft:
    """Pre-embed shape — caller fills the basics and the index handles embedding."""

    source_type: LessonSource
    source_ref: str               # canonical id in the source store
    content: str                  # what the embedder + LLM see
    metadata: dict[str, str | int | float | bool | None]


class LessonsIndex:
    """Publish/search facade in front of :class:`LessonsLanceAdapter`."""

    def __init__(
        self,
        adapter: LessonsLanceAdapter,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        log.memory.debug(
            "[learning] index.init: ready",
            extra={"_fields": {
                "has_embedding_registry": embedding_registry is not None,
            }},
        )
        self._adapter = adapter
        self._embedder = embedding_registry

    async def publish(self, draft: LessonDraft) -> bool:
        """Embed ``draft.content`` and upsert into the lessons table.

        Returns ``True`` on success, ``False`` when no embedding registry is
        wired (tests/dry-run) or the embed call fails. Best-effort — lessons
        are an enhancement layer, not gating.
        """
        log.memory.debug(
            "[learning] index.publish: entry",
            extra={"_fields": {
                "source_type": draft.source_type,
                "source_ref": draft.source_ref,
                "content_len": len(draft.content),
            }},
        )
        if self._embedder is None:
            log.memory.debug(
                "[learning] index.publish: exit — no embedding registry",
            )
            return False
        if not draft.content.strip():
            return False
        try:
            vectors = await self._embedder.get().embed([draft.content])
        except Exception as exc:  # B5
            log.memory.warning(
                "[learning] index.publish: embed failed — skipping",
                exc_info=exc,
                extra={"_fields": {"source_type": draft.source_type}},
            )
            return False
        if not vectors or not vectors[0]:
            return False
        lesson = Lesson(
            lesson_id=make_lesson_id(draft.source_type, draft.source_ref),
            source_type=draft.source_type,
            source_ref=draft.source_ref,
            content=draft.content,
            embedding=list(vectors[0]),
            metadata=dict(draft.metadata),
        )
        try:
            await self._adapter.publish(lesson)
        except Exception as exc:  # B5
            log.memory.warning(
                "[learning] index.publish: adapter.publish failed — skipping",
                exc_info=exc,
                extra={"_fields": {"lesson_id": lesson.lesson_id}},
            )
            return False
        log.memory.info(
            "[learning] index.publish: stored",
            extra={"_fields": {"lesson_id": lesson.lesson_id}},
        )
        return True

    async def publish_many(self, drafts: list[LessonDraft]) -> int:
        """Embed + batch-publish. Returns the count written."""
        if not drafts:
            return 0
        log.memory.debug(
            "[learning] index.publish_many: entry",
            extra={"_fields": {"n": len(drafts)}},
        )
        if self._embedder is None:
            log.memory.debug("[learning] index.publish_many: exit — no embedder")
            return 0
        texts = [d.content for d in drafts if d.content.strip()]
        if not texts:
            return 0
        try:
            vectors = await self._embedder.get().embed(texts)
        except Exception as exc:  # B5
            log.memory.warning(
                "[learning] index.publish_many: embed batch failed",
                exc_info=exc,
            )
            return 0
        lessons: list[Lesson] = []
        # Pair vectors back to drafts that had content (vectors aligns to texts order).
        text_iter = iter(vectors)
        for d in drafts:
            if not d.content.strip():
                continue
            try:
                vec = next(text_iter)
            except StopIteration:
                break
            lessons.append(Lesson(
                lesson_id=make_lesson_id(d.source_type, d.source_ref),
                source_type=d.source_type,
                source_ref=d.source_ref,
                content=d.content,
                embedding=list(vec),
                metadata=dict(d.metadata),
            ))
        try:
            written = await self._adapter.publish_many(lessons)
        except Exception as exc:  # B5
            log.memory.warning(
                "[learning] index.publish_many: adapter call failed",
                exc_info=exc,
            )
            return 0
        log.memory.info(
            "[learning] index.publish_many: exit",
            extra={"_fields": {"written": written}},
        )
        return written

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        source_filter: LessonSource | None = None,
    ) -> list[LessonHit]:
        """Embed ``query`` and ANN over the lessons table.

        Returns ``[]`` when the embedder isn't wired, the query is empty, or
        the ANN call fails — callers must treat absence as "no signal" and
        keep going.
        """
        log.memory.debug(
            "[learning] index.search: entry",
            extra={"_fields": {
                "query_len": len(query), "limit": limit, "source_filter": source_filter,
            }},
        )
        if self._embedder is None or not query.strip():
            return []
        try:
            vectors = await self._embedder.get().embed([query])
        except Exception as exc:  # B5
            log.memory.warning(
                "[learning] index.search: embed failed",
                exc_info=exc, extra={"_fields": {"query_len": len(query)}},
            )
            return []
        if not vectors or not vectors[0]:
            return []
        hits = await self._adapter.search(
            list(vectors[0]), limit=limit, source_filter=source_filter,
        )
        log.memory.debug(
            "[learning] index.search: exit",
            extra={"_fields": {"n_hits": len(hits)}},
        )
        return hits

    async def delete(self, source_type: LessonSource, source_ref: str) -> None:
        """Remove a lesson from the index (caller deletes from source store separately)."""
        await self._adapter.delete(make_lesson_id(source_type, source_ref))
