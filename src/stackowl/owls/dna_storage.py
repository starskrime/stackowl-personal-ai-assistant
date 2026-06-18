"""DNACheckpointer — persists OwlDNA snapshots to SQLite for rollback.

Also exports :func:`upsert_owl_dna`, the single shared helper that writes the 6
trait columns into either ``owl_dna`` (evolved store) or ``owl_dna_authored``
(baseline/authored store). Centralising the upsert here removes the duplicate
``_UPSERT_DNA_SQL`` / ``_persist_dna`` logic from ``evolution.py`` (DRY).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.exceptions import ManifestValidationError
from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

# ---------------------------------------------------------------------------
# Shared upsert helper — owl_dna and owl_dna_authored
# ---------------------------------------------------------------------------

_ALLOWED_DNA_TABLES: frozenset[str] = frozenset({"owl_dna", "owl_dna_authored"})


async def upsert_owl_dna(
    db: DbPool,
    owl_name: str,
    dna: OwlDNA,
    *,
    table: str = "owl_dna",
) -> None:
    """Upsert the 6 trait columns + updated_at for an owl into *table*.

    *table* must be one of ``"owl_dna"`` (evolved store) or
    ``"owl_dna_authored"`` (authored/baseline store). The column order follows
    the canonical :data:`~stackowl.owls.dna_defaults.TRAIT_NAMES` tuple so
    positional transposition is impossible.

    Raises :class:`ValueError` for any unknown table name (SQL-injection guard).
    """
    log.engine.debug(
        "[dna] upsert_owl_dna: entry",
        extra={"_fields": {"owl": owl_name, "table": table}},
    )
    if table not in _ALLOWED_DNA_TABLES:
        raise ValueError(f"upsert_owl_dna: unknown table {table!r}")

    cols = ", ".join(TRAIT_NAMES)
    placeholders = ", ".join("?" for _ in TRAIT_NAMES)
    set_clause = ", ".join(f"{t} = excluded.{t}" for t in TRAIT_NAMES)
    sql = (
        f"INSERT INTO {table} (owl_name, {cols}, updated_at) "
        f"VALUES (?, {placeholders}, ?) "
        f"ON CONFLICT(owl_name) DO UPDATE SET {set_clause}, updated_at = excluded.updated_at"
    )
    values = (
        owl_name,
        *(float(getattr(dna, t)) for t in TRAIT_NAMES),
        datetime.now(UTC).isoformat(),
    )
    try:
        await db.execute(sql, values)
    except Exception as exc:
        log.engine.error(
            "[dna] upsert_owl_dna: db write failed",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name, "table": table}},
        )
        raise
    log.engine.debug(
        "[dna] upsert_owl_dna: exit",
        extra={"_fields": {"owl": owl_name, "table": table}},
    )

_INSERT_SQL = """
INSERT INTO dna_checkpoints (
    owl_name, checkpoint_id, challenge_level, verbosity,
    curiosity, formality, creativity, precision, reason, created_at, owner_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_BY_ID_SQL = """
SELECT challenge_level, verbosity, curiosity, formality, creativity, precision
FROM dna_checkpoints
WHERE owner_id = ? AND owl_name = ? AND checkpoint_id = ?
"""

_LIST_SQL = """
SELECT checkpoint_id, challenge_level, verbosity, curiosity,
       formality, creativity, precision, reason, created_at
FROM dna_checkpoints
WHERE owner_id = ? AND owl_name = ?
ORDER BY created_at DESC
LIMIT ?
"""

_DNA_FIELDS: tuple[str, ...] = TRAIT_NAMES


class DNACheckpointer(OwnedRepository):
    """Persists DNA checkpoints to SQLite for rollback.

    Snapshots are stored in the ``dna_checkpoints`` table created by migration
    0012. Each checkpoint is identified by a UUID4 ``checkpoint_id`` and tagged
    with a free-form ``reason`` (defaults to ``"auto"``). Owner-scoped: reads/
    writes are constrained to ``owner_id`` (defaults to the single-user
    :data:`DEFAULT_PRINCIPAL_ID`, so existing behavior is unchanged).
    """

    _table = "dna_checkpoints"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    async def checkpoint(self, owl_name: str, dna: OwlDNA, reason: str = "auto") -> str:
        """Save current DNA state and return the generated ``checkpoint_id``."""
        log.engine.debug(
            "[dna] checkpointer.checkpoint: entry",
            extra={"_fields": {"owl": owl_name, "reason": reason}},
        )
        checkpoint_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        try:
            await self._db.execute(
                _INSERT_SQL,
                (
                    owl_name,
                    checkpoint_id,
                    dna.challenge_level,
                    dna.verbosity,
                    dna.curiosity,
                    dna.formality,
                    dna.creativity,
                    dna.precision,
                    reason,
                    created_at,
                    self._owner_id,
                ),
            )
        except Exception as exc:
            log.engine.error(
                "[dna] checkpointer.checkpoint: db write failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name, "checkpoint_id": checkpoint_id}},
            )
            raise
        log.engine.info(
            "[dna] checkpointer.checkpoint: exit",
            extra={
                "_fields": {
                    "owl": owl_name,
                    "checkpoint_id": checkpoint_id,
                    "reason": reason,
                }
            },
        )
        return checkpoint_id

    async def restore(self, owl_name: str, checkpoint_id: str) -> OwlDNA:
        """Load and return DNA from a saved checkpoint."""
        log.engine.debug(
            "[dna] checkpointer.restore: entry",
            extra={"_fields": {"owl": owl_name, "checkpoint_id": checkpoint_id}},
        )
        try:
            rows = await self._db.fetch_all(
                _SELECT_BY_ID_SQL, (self._owner_id, owl_name, checkpoint_id),
            )
        except Exception as exc:
            log.engine.error(
                "[dna] checkpointer.restore: db read failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name, "checkpoint_id": checkpoint_id}},
            )
            raise
        if not rows:
            log.engine.warning(
                "[dna] checkpointer.restore: checkpoint not found",
                extra={"_fields": {"owl": owl_name, "checkpoint_id": checkpoint_id}},
            )
            raise ManifestValidationError(
                "checkpoint_id",
                f"No checkpoint {checkpoint_id!r} for owl {owl_name!r}",
            )
        row = rows[0]
        values: dict[str, float] = {field: float(row[field]) for field in _DNA_FIELDS}
        dna = OwlDNA(**values)
        log.engine.info(
            "[dna] checkpointer.restore: exit",
            extra={"_fields": {"owl": owl_name, "checkpoint_id": checkpoint_id}},
        )
        return dna

    async def list_checkpoints(self, owl_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent checkpoints for ``owl_name`` (newest first)."""
        log.engine.debug(
            "[dna] checkpointer.list: entry",
            extra={"_fields": {"owl": owl_name, "limit": limit}},
        )
        if limit < 1:
            log.engine.warning(
                "[dna] checkpointer.list: non-positive limit coerced to 1",
                extra={"_fields": {"owl": owl_name, "requested": limit}},
            )
            limit = 1
        try:
            rows = await self._db.fetch_all(_LIST_SQL, (self._owner_id, owl_name, limit))
        except Exception as exc:
            log.engine.error(
                "[dna] checkpointer.list: db read failed",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name}},
            )
            raise
        log.engine.debug(
            "[dna] checkpointer.list: exit",
            extra={"_fields": {"owl": owl_name, "count": len(rows)}},
        )
        return rows
