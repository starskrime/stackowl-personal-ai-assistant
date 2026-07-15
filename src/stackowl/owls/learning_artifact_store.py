"""LearningArtifactStore — unified snapshot/restore/audit primitive.

One ``learning_artifacts`` table (migration 0087) serves BOTH DNA and skill
mutations, and BOTH the snapshot (rollback) role AND the audit-trail role:
each row's ``(artifact_id, reason, created_at)`` already records what changed,
why, and when (FR-3), while ``payload_json`` is the restorable snapshot
(FR-1). No separate audit table exists — see Story 2.1 Dev Notes (AD-2).

This module builds the primitive only; no existing call site (DNA evolution,
skill mutation) is wired onto it yet — that is Story 2.3.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from stackowl.db.pool import DbPool
from stackowl.exceptions import ManifestValidationError
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

ArtifactType = Literal["dna", "skill"]


class LearningArtifactStore(OwnedRepository):
    """Owner-scoped snapshot/restore/audit primitive shared by DNA and skills.

    Snapshots are stored in the ``learning_artifacts`` table created by
    migration 0087. Each checkpoint is identified by a UUID4
    ``checkpoint_id`` and tagged with a free-form ``reason`` (defaults to
    ``"auto"``). Owner-scoped: reads/writes are constrained to ``owner_id``
    (defaults to the single-user :data:`DEFAULT_PRINCIPAL_ID`).
    """

    _table = "learning_artifacts"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)

    async def checkpoint(
        self,
        artifact_type: ArtifactType,
        artifact_id: str,
        payload: dict[str, object],
        reason: str = "auto",
    ) -> str:
        """Snapshot *payload* and return the generated ``checkpoint_id``.

        This single row is both the rollback snapshot (FR-1) and the audit
        record (FR-3) — see module docstring.
        """
        # 1. ENTRY
        log.owls.debug(
            "[learning_artifact_store] checkpoint: entry",
            extra={"_fields": {
                "artifact_type": artifact_type, "artifact_id": artifact_id, "reason": reason,
            }},
        )
        checkpoint_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        try:
            # 3. STEP — single owner-stamped insert
            await self._insert_owned(
                self._table,
                {
                    "artifact_type": artifact_type,
                    "artifact_id": artifact_id,
                    "checkpoint_id": checkpoint_id,
                    "payload_json": json.dumps(payload),
                    "reason": reason,
                    "created_at": created_at,
                },
            )
        except Exception as exc:
            log.owls.error(
                "[learning_artifact_store] checkpoint: db write failed",
                exc_info=exc,
                extra={"_fields": {
                    "artifact_type": artifact_type, "artifact_id": artifact_id,
                    "checkpoint_id": checkpoint_id,
                }},
            )
            raise
        # 4. EXIT
        log.owls.info(
            "[learning_artifact_store] checkpoint: exit",
            extra={"_fields": {
                "artifact_type": artifact_type, "artifact_id": artifact_id,
                "checkpoint_id": checkpoint_id, "reason": reason,
            }},
        )
        return checkpoint_id

    async def restore(
        self, artifact_type: str, artifact_id: str, checkpoint_id: str,
    ) -> dict[str, object]:
        """Load and return the exact payload from a saved checkpoint.

        Raises :class:`ManifestValidationError` if no such checkpoint exists
        for this owner/artifact — never a silent ``None``/empty-dict return.
        """
        # 1. ENTRY
        log.owls.debug(
            "[learning_artifact_store] restore: entry",
            extra={"_fields": {
                "artifact_type": artifact_type, "artifact_id": artifact_id,
                "checkpoint_id": checkpoint_id,
            }},
        )
        try:
            # 3. STEP — owner-scoped read
            rows = await self._fetch_owned(
                self._table,
                "artifact_type = ? AND artifact_id = ? AND checkpoint_id = ?",
                (artifact_type, artifact_id, checkpoint_id),
            )
        except Exception as exc:
            log.owls.error(
                "[learning_artifact_store] restore: db read failed",
                exc_info=exc,
                extra={"_fields": {
                    "artifact_type": artifact_type, "artifact_id": artifact_id,
                    "checkpoint_id": checkpoint_id,
                }},
            )
            raise
        if not rows:
            # 2. DECISION — no matching row: fail loud, never a silent empty return
            log.owls.warning(
                "[learning_artifact_store] restore: checkpoint not found",
                extra={"_fields": {
                    "artifact_type": artifact_type, "artifact_id": artifact_id,
                    "checkpoint_id": checkpoint_id,
                }},
            )
            raise ManifestValidationError(
                "checkpoint_id",
                f"No checkpoint {checkpoint_id!r} for {artifact_type}:{artifact_id!r}",
            )
        payload: dict[str, object] = json.loads(rows[0]["payload_json"])
        # 4. EXIT
        log.owls.info(
            "[learning_artifact_store] restore: exit",
            extra={"_fields": {
                "artifact_type": artifact_type, "artifact_id": artifact_id,
                "checkpoint_id": checkpoint_id,
            }},
        )
        return payload

    async def list_checkpoints(
        self, artifact_type: str, artifact_id: str, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return the most recent checkpoints for this artifact (newest first)."""
        # 1. ENTRY
        log.owls.debug(
            "[learning_artifact_store] list_checkpoints: entry",
            extra={"_fields": {
                "artifact_type": artifact_type, "artifact_id": artifact_id, "limit": limit,
            }},
        )
        if limit < 1:
            # 2. DECISION — non-positive limit coerced, not raised (mirrors DNACheckpointer)
            log.owls.warning(
                "[learning_artifact_store] list_checkpoints: non-positive limit coerced to 1",
                extra={"_fields": {
                    "artifact_type": artifact_type, "artifact_id": artifact_id, "requested": limit,
                }},
            )
            limit = 1
        try:
            # 3. STEP — owner-scoped read, sorted+sliced in Python (OwnedRepository
            # convention: _fetch_owned has no ORDER BY/LIMIT support)
            rows = await self._fetch_owned(
                self._table,
                "artifact_type = ? AND artifact_id = ?",
                (artifact_type, artifact_id),
            )
        except Exception as exc:
            log.owls.error(
                "[learning_artifact_store] list_checkpoints: db read failed",
                exc_info=exc,
                extra={"_fields": {"artifact_type": artifact_type, "artifact_id": artifact_id}},
            )
            raise
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        result = rows[:limit]
        # 4. EXIT
        log.owls.debug(
            "[learning_artifact_store] list_checkpoints: exit",
            extra={"_fields": {
                "artifact_type": artifact_type, "artifact_id": artifact_id, "count": len(result),
            }},
        )
        return result
