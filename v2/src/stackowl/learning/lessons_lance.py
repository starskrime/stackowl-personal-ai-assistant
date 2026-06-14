"""LanceDB lessons table — async wrapper around the second table on the same
LanceDB connection that already serves ``committed_facts``.

Lessons share the existing LanceDB on-disk directory + connection. They live
in a separate table (``lessons``) with a richer row schema:
``lesson_id`` PK, ``source_type``, ``source_ref``, ``content``, ``embedding``,
``metadata`` (JSON).

Per [[feedback_use_existing_infrastructure]]: there is exactly one ANN
backend and we federate by sharing it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.learning.lesson import Lesson, LessonHit

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from lancedb import DBConnection  # type: ignore[import-untyped]
    from lancedb.table import Table  # type: ignore[import-untyped]


_LESSONS_TABLE = "lessons"


def _default_data_dir() -> Path:
    from stackowl.paths import StackowlHome
    return StackowlHome.lancedb_dir()


class LessonsLanceAdapter:
    """Async wrapper around the LanceDB ``lessons`` table."""

    def __init__(self, data_dir: Path | None = None) -> None:
        log.memory.debug(
            "[learning] lessons_lance.init: entry",
            extra={"_fields": {
                "data_dir": str(data_dir) if data_dir else "<default>",
            }},
        )
        self._data_dir = data_dir or _default_data_dir()
        self._connection: DBConnection | None = None

    # ----- public async API ------------------------------------------------

    async def publish(self, lesson: Lesson) -> None:
        """Upsert one lesson into the LanceDB table."""
        log.memory.debug(
            "[learning] lessons_lance.publish: entry",
            extra={"_fields": {
                "lesson_id": lesson.lesson_id,
                "source_type": lesson.source_type,
                "dim": len(lesson.embedding),
            }},
        )
        if not lesson.embedding:
            log.memory.warning(
                "[learning] lessons_lance.publish: empty embedding — skipping",
                extra={"_fields": {"lesson_id": lesson.lesson_id}},
            )
            return
        TestModeGuard.assert_not_test_mode("lessons_lance.publish")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, _sync_publish, self._connect(), lesson,
        )
        log.memory.info(
            "[learning] lessons_lance.publish: stored",
            extra={"_fields": {"lesson_id": lesson.lesson_id}},
        )

    async def publish_many(self, lessons: list[Lesson]) -> int:
        """Batch upsert. Returns the count actually published."""
        if not lessons:
            return 0
        log.memory.debug(
            "[learning] lessons_lance.publish_many: entry",
            extra={"_fields": {"n": len(lessons)}},
        )
        TestModeGuard.assert_not_test_mode("lessons_lance.publish_many")
        loop = asyncio.get_event_loop()
        valid = [le for le in lessons if le.embedding]
        if not valid:
            return 0
        await loop.run_in_executor(
            None, _sync_publish_many, self._connect(), valid,
        )
        log.memory.info(
            "[learning] lessons_lance.publish_many: stored",
            extra={"_fields": {"published": len(valid), "skipped": len(lessons) - len(valid)}},
        )
        return len(valid)

    async def search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        source_filter: str | None = None,
    ) -> list[LessonHit]:
        """ANN query over lessons. ``source_filter`` is optional source_type filter."""
        log.memory.debug(
            "[learning] lessons_lance.search: entry",
            extra={"_fields": {
                "limit": limit, "source_filter": source_filter,
                "dim": len(query_embedding),
            }},
        )
        if not query_embedding:
            return []
        TestModeGuard.assert_not_test_mode("lessons_lance.search")
        loop = asyncio.get_event_loop()
        try:
            hits = await loop.run_in_executor(
                None, _sync_search,
                self._connect(), list(query_embedding), limit, source_filter,
            )
        except Exception as exc:  # B5 — never crash callers on ANN failure
            log.memory.warning(
                "[learning] lessons_lance.search: failed — returning []",
                exc_info=exc,
            )
            return []
        log.memory.debug(
            "[learning] lessons_lance.search: exit",
            extra={"_fields": {"n_hits": len(hits)}},
        )
        return hits

    async def delete(self, lesson_id: str) -> None:
        """Remove a lesson by id (used when underlying skill is deprecated)."""
        log.memory.debug(
            "[learning] lessons_lance.delete: entry",
            extra={"_fields": {"lesson_id": lesson_id}},
        )
        TestModeGuard.assert_not_test_mode("lessons_lance.delete")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_delete, self._connect(), lesson_id)
        log.memory.info(
            "[learning] lessons_lance.delete: removed",
            extra={"_fields": {"lesson_id": lesson_id}},
        )

    # ----- internal --------------------------------------------------------

    def _connect(self) -> DBConnection:
        if self._connection is None:
            import lancedb as _lance

            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._connection = _lance.connect(str(self._data_dir))
        return self._connection


# ----- sync executor bodies (kept module-private) --------------------------


def _table_names(conn: DBConnection) -> list[str]:
    """Same gotcha as the committed_facts helpers — ``list_tables()`` returns a
    ``ListTablesResponse`` object, not a plain list. Normalize to ``list[str]``."""
    response = conn.list_tables()
    names = getattr(response, "tables", None)
    if names is None:
        try:
            return list(response)
        except TypeError:
            log.memory.warning(
                "[learning] lessons_lance._table_names: response not iterable — treating as empty",
                extra={"_fields": {"type": type(response).__name__}},
            )
            return []
    return list(names)


def _make_schema(dim: int) -> Any:
    import pyarrow as pa

    return pa.schema([
        pa.field("lesson_id", pa.string()),
        pa.field("source_type", pa.string()),
        pa.field("source_ref", pa.string()),
        pa.field("content", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), dim)),
        pa.field("metadata", pa.string()),
    ])


def _make_row(lesson: Lesson) -> dict[str, Any]:
    return {
        "lesson_id": lesson.lesson_id,
        "source_type": lesson.source_type,
        "source_ref": lesson.source_ref,
        "content": lesson.content,
        "embedding": list(lesson.embedding),
        "metadata": json.dumps(dict(lesson.metadata), default=str),
    }


def _get_or_create_table(conn: DBConnection, dim: int) -> Table:
    if _LESSONS_TABLE not in _table_names(conn):
        log.memory.info(
            "[learning] lessons_lance: creating lessons table",
            extra={"_fields": {"table": _LESSONS_TABLE, "dim": dim}},
        )
        conn.create_table(_LESSONS_TABLE, schema=_make_schema(dim), exist_ok=True)
    return conn.open_table(_LESSONS_TABLE)


def _sync_publish(conn: DBConnection, lesson: Lesson) -> None:
    table = _get_or_create_table(conn, len(lesson.embedding))
    (
        table.merge_insert("lesson_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute([_make_row(lesson)])
    )


def _sync_publish_many(conn: DBConnection, lessons: list[Lesson]) -> None:
    first_dim = len(lessons[0].embedding)
    table = _get_or_create_table(conn, first_dim)
    rows = [_make_row(le) for le in lessons]
    (
        table.merge_insert("lesson_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(rows)
    )


def _sync_search(
    conn: DBConnection, query_embedding: list[float],
    limit: int, source_filter: str | None,
) -> list[LessonHit]:
    if _LESSONS_TABLE not in _table_names(conn):
        return []
    table = conn.open_table(_LESSONS_TABLE)
    query = table.search(query_embedding).limit(limit)
    if source_filter:
        escaped = source_filter.replace("'", "''")
        query = query.where(f"source_type = '{escaped}'")
    rows = query.to_list()
    hits: list[LessonHit] = []
    for raw in rows:
        try:
            metadata = json.loads(raw.get("metadata") or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except json.JSONDecodeError as exc:
            log.memory.warning(
                "[learning] lessons_lance.search: corrupt metadata JSON — using empty",
                exc_info=exc,
                extra={"_fields": {"lesson_id": raw.get("lesson_id")}},
            )
            metadata = {}
        distance = float(raw.get("_distance", 0.0))
        similarity = 1.0 / (1.0 + distance)
        hits.append(LessonHit(
            lesson_id=raw["lesson_id"],
            source_type=raw["source_type"],
            source_ref=raw["source_ref"],
            content=raw.get("content", ""),
            similarity=similarity,
            metadata=metadata,
        ))
    return hits


def _sync_delete(conn: DBConnection, lesson_id: str) -> None:
    if _LESSONS_TABLE not in _table_names(conn):
        return
    table = conn.open_table(_LESSONS_TABLE)
    escaped = lesson_id.replace("'", "''")
    table.delete(f"lesson_id = '{escaped}'")
