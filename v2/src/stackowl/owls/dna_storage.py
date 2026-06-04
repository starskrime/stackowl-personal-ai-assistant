"""DNACheckpointer — persists OwlDNA snapshots to SQLite for rollback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.exceptions import ManifestValidationError
from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

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

_DNA_FIELDS: tuple[str, ...] = (
    "challenge_level",
    "verbosity",
    "curiosity",
    "formality",
    "creativity",
    "precision",
)


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
