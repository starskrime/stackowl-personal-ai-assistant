"""SkillIndexStore — SQLite cache + audit log over the skills/ workspace.

Files are source of truth (one directory per skill under
``~/.stackowl/workspace/skills/<source>/<name>/``). This store caches the
parsed manifest + body + embedding for fast retrieval, plus a ``skill_audit``
forensic trail so ``/skill diff`` and ``/skill restore`` can show every agent
edit. Mirrors :class:`TaskOutcomeStore` (Commit 1) and :class:`ReflectionStore`
(Commit 2).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import pack_embedding, unpack_embedding
from stackowl.skills.manifest import SkillSource
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

if TYPE_CHECKING:
    from stackowl.skills.loader import LoadedSkill


@dataclass(frozen=True)
class Skill:
    """Read-side projection of one ``skills`` row."""

    skill_id: int
    name: str
    source: SkillSource
    path: str
    description: str
    when_to_use: str
    version: str
    enabled: bool
    success_rate: float | None
    n_executions: int
    parent_traces: list[str]
    embedding: list[float] | None
    embedding_model: str | None
    body_text: str
    manifest_json: dict[str, object]
    loaded_at: float
    updated_at: float


@dataclass(frozen=True)
class SkillAuditEntry:
    """Read-side projection of one ``skill_audit`` row."""

    audit_id: int
    skill_id: int | None
    skill_name: str
    source: SkillSource
    op: str
    actor: str
    before_hash: str | None
    after_hash: str | None
    details: dict[str, object]
    snapshot: dict[str, str]
    ts: float


_UPSERT_SQL = """
INSERT INTO skills (
    name, source, path, description, when_to_use, version, enabled,
    success_rate, n_executions, parent_traces, embedding, embedding_model,
    manifest_json, body_text, loaded_at, updated_at, owner_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(owner_id, source, name) DO UPDATE SET
    path = excluded.path,
    description = excluded.description,
    when_to_use = excluded.when_to_use,
    version = excluded.version,
    enabled = excluded.enabled,
    parent_traces = excluded.parent_traces,
    manifest_json = excluded.manifest_json,
    body_text = excluded.body_text,
    updated_at = excluded.updated_at
"""

_SELECT_FIELDS = """
    skill_id, name, source, path, description, when_to_use, version, enabled,
    success_rate, n_executions, parent_traces, embedding, embedding_model,
    manifest_json, body_text, loaded_at, updated_at
"""


class SkillIndexStore(OwnedRepository):
    """Async SQLite wrapper for the ``skills`` + ``skill_audit`` tables (migration 0031).

    Owner-scoped on the ``skills`` table: reads/writes are constrained to
    ``owner_id`` (defaults to the single-user :data:`DEFAULT_PRINCIPAL_ID`, so
    existing behavior is unchanged). The ``skill_audit`` forensic trail has no
    ``owner_id`` column and is left unscoped.
    """

    _table = "skills"

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        super().__init__(db, owner_id)
        log.skills.debug("[skills] store.init: ready")

    async def upsert(self, loaded: LoadedSkill) -> int:
        """Insert or update a skills row from a :class:`LoadedSkill`.

        ON CONFLICT preserves runtime-managed fields (``success_rate``,
        ``n_executions``, ``embedding``) so a re-scan never wipes the agent's
        learning bookkeeping.
        """
        # 1. ENTRY
        m = loaded.manifest
        log.skills.debug(
            "[skills] store.upsert: entry",
            extra={"_fields": {"name": m.name, "source": m.source, "path": str(loaded.path)}},
        )
        # 3. STEP — serialize manifest + parent_traces
        manifest_json = json.dumps(m.model_dump(mode="json"), separators=(",", ":"))
        parent_traces = json.dumps(list(m.parent_traces), separators=(",", ":"))
        now = time.time()
        await self._db.execute(
            _UPSERT_SQL,
            (
                m.name, m.source, str(loaded.path), m.description, m.when_to_use,
                m.version, int(m.enabled), m.success_rate, m.n_executions,
                parent_traces, None, m.embedding_model, manifest_json,
                loaded.body, now, now, self._owner_id,
            ),
        )
        # Find the row id for the row we just upserted (caller may need it).
        rows = await self._db.fetch_all(
            "SELECT skill_id FROM skills WHERE owner_id = ? AND source = ? AND name = ?",
            (self._owner_id, m.source, m.name),
        )
        skill_id = int(str(rows[0]["skill_id"])) if rows else -1
        # 4. EXIT
        log.skills.info(
            "[skills] store.upsert: stored",
            extra={"_fields": {"name": m.name, "source": m.source, "skill_id": skill_id}},
        )
        return skill_id

    async def list_for_source(self, source: SkillSource) -> list[Skill]:
        """Return every skill in ``source``, ordered by name."""
        # 1. ENTRY
        log.skills.debug("[skills] store.list_for_source: entry",
                  extra={"_fields": {"source": source}})
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM skills "
            "WHERE owner_id = ? AND source = ? ORDER BY name",
            (self._owner_id, source),
        )
        results = [_row_to_skill(r) for r in rows]
        # 4. EXIT
        log.skills.debug("[skills] store.list_for_source: exit",
                  extra={"_fields": {"source": source, "count": len(results)}})
        return results

    async def list_enabled(self) -> list[Skill]:
        """Return every enabled skill across all sources."""
        # 1. ENTRY
        log.skills.debug("[skills] store.list_enabled: entry")
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM skills "
            "WHERE owner_id = ? AND enabled = 1 ORDER BY source, name",
            (self._owner_id,),
        )
        results = [_row_to_skill(r) for r in rows]
        # 4. EXIT
        log.skills.debug("[skills] store.list_enabled: exit",
                  extra={"_fields": {"count": len(results)}})
        return results

    async def get(self, source: SkillSource, name: str) -> Skill | None:
        """Return one skill by (source, name) or ``None`` if missing."""
        # 1. ENTRY
        log.skills.debug("[skills] store.get: entry",
                  extra={"_fields": {"source": source, "name": name}})
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM skills "
            "WHERE owner_id = ? AND source = ? AND name = ?",
            (self._owner_id, source, name),
        )
        # 2. DECISION + 4. EXIT
        if not rows:
            log.skills.debug("[skills] store.get: exit — miss",
                      extra={"_fields": {"source": source, "name": name}})
            return None
        sk = _row_to_skill(rows[0])
        log.skills.debug("[skills] store.get: exit — hit",
                  extra={"_fields": {"skill_id": sk.skill_id}})
        return sk

    async def set_enabled(self, skill_id: int, *, enabled: bool) -> None:
        """Toggle the enabled flag (used by /skill enable / disable)."""
        # 1. ENTRY
        log.skills.debug("[skills] store.set_enabled: entry",
                  extra={"_fields": {"skill_id": skill_id, "enabled": enabled}})
        await self._db.execute(
            "UPDATE skills SET enabled = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (int(enabled), time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.info("[skills] store.set_enabled: stored",
                 extra={"_fields": {"skill_id": skill_id, "enabled": enabled}})

    async def set_embedding(
        self, skill_id: int, embedding: list[float] | None, model: str | None,
    ) -> None:
        """Write the embedding back. Used by classify/synthesizer paths."""
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.set_embedding: entry",
            extra={"_fields": {
                "skill_id": skill_id, "has_embedding": embedding is not None,
                "model": model,
            }},
        )
        blob = pack_embedding(embedding) if embedding else None
        await self._db.execute(
            "UPDATE skills SET embedding = ?, embedding_model = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (blob, model, time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.info("[skills] store.set_embedding: stored",
                 extra={"_fields": {"skill_id": skill_id}})

    async def increment_n_executions(self, skill_id: int) -> None:
        """Bump n_executions by 1. Called when the agent uses a skill."""
        # 1. ENTRY
        log.skills.debug("[skills] store.increment_n_executions: entry",
                  extra={"_fields": {"skill_id": skill_id}})
        await self._db.execute(
            "UPDATE skills SET n_executions = n_executions + 1, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.debug("[skills] store.increment_n_executions: exit",
                  extra={"_fields": {"skill_id": skill_id}})

    async def set_success_rate(self, skill_id: int, rate: float) -> None:
        """Overwrite the EWMA success rate (caller computes it)."""
        # 1. ENTRY
        log.skills.debug("[skills] store.set_success_rate: entry",
                  extra={"_fields": {"skill_id": skill_id, "rate": rate}})
        if rate < 0.0 or rate > 1.0:
            log.skills.warning(
                "[skills] store.set_success_rate: rate out of range — clamping",
                extra={"_fields": {"skill_id": skill_id, "rate": rate}},
            )
            rate = max(0.0, min(1.0, rate))
        await self._db.execute(
            "UPDATE skills SET success_rate = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (rate, time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.info("[skills] store.set_success_rate: stored",
                 extra={"_fields": {"skill_id": skill_id, "rate": rate}})

    async def semantic_recall(
        self,
        query_embedding: list[float],
        *,
        limit: int = 3,
        min_similarity: float = 0.0,
    ) -> list[tuple[Skill, float]]:
        """Cosine recall over enabled skills with embeddings.

        Returns ``(skill, similarity)`` pairs ordered by similarity descending,
        truncated to ``limit``. Skills without an embedding are skipped silently;
        returning ``[]`` is always safe (caller decides whether absence is an
        error). Suitable for the small N (≤hundreds) of skills the workspace
        will hold — for larger corpora switch to LanceDB at the SkillsAssembly
        layer.
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.semantic_recall: entry",
            extra={"_fields": {"dim": len(query_embedding), "limit": limit}},
        )
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM skills "
            "WHERE owner_id = ? AND enabled = 1 AND embedding IS NOT NULL",
            (self._owner_id,),
        )
        if not rows:
            log.skills.debug("[skills] store.semantic_recall: exit — no candidates")
            return []
        import numpy as np

        q = np.asarray(query_embedding, dtype="<f4")
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            log.skills.debug("[skills] store.semantic_recall: exit — zero query vec")
            return []
        # 3. STEP — score each candidate by cosine; skip dim mismatches
        scored: list[tuple[Skill, float]] = []
        for r in rows:
            sk = _row_to_skill(r)
            if not sk.embedding or len(sk.embedding) != len(query_embedding):
                continue
            v = np.asarray(sk.embedding, dtype="<f4")
            v_norm = float(np.linalg.norm(v))
            if v_norm == 0.0:
                continue
            sim = float(np.dot(q, v) / (q_norm * v_norm))
            if sim < min_similarity:
                continue
            scored.append((sk, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:limit]
        # 4. EXIT
        log.skills.debug(
            "[skills] store.semantic_recall: exit",
            extra={"_fields": {
                "candidates": len(rows), "returned": len(results),
                "top_sim": results[0][1] if results else None,
            }},
        )
        return results

    async def delete(self, skill_id: int) -> None:
        """Remove the index row. File system deletion is the caller's job."""
        # 1. ENTRY
        log.skills.debug("[skills] store.delete: entry",
                  extra={"_fields": {"skill_id": skill_id}})
        await self._db.execute(
            "DELETE FROM skills WHERE skill_id = ? AND owner_id = ?",
            (skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.info("[skills] store.delete: deleted",
                 extra={"_fields": {"skill_id": skill_id}})

    # ----- audit ------------------------------------------------------------

    async def audit_write(
        self,
        *,
        skill_name: str,
        source: SkillSource,
        op: str,
        actor: str,
        skill_id: int | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        details: dict[str, object] | None = None,
        snapshot: dict[str, str] | None = None,
    ) -> None:
        """Append one row to ``skill_audit``.

        ``op`` ∈ {create, update, delete, enable, disable, deprecate, restore}.
        ``snapshot`` is the file-tree snapshot used by ``/skill restore``;
        ``{}`` is valid for ops that don't change content (enable/disable).
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.audit_write: entry",
            extra={"_fields": {
                "skill_name": skill_name, "source": source, "op": op, "actor": actor,
                "snapshot_files": len(snapshot) if snapshot else 0,
            }},
        )
        details_json = json.dumps(details or {}, separators=(",", ":"))
        snapshot_json = json.dumps(snapshot or {}, separators=(",", ":"))
        await self._db.execute(
            """INSERT INTO skill_audit
                   (skill_id, skill_name, source, op, actor,
                    before_hash, after_hash, details, ts, snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                skill_id, skill_name, source, op, actor,
                before_hash, after_hash, details_json, time.time(),
                snapshot_json,
            ),
        )
        # 4. EXIT
        log.skills.info(
            "[skills] store.audit_write: stored",
            extra={"_fields": {
                "skill_name": skill_name, "op": op, "actor": actor,
                "snapshot_bytes": len(snapshot_json),
            }},
        )

    async def find_audit_by_hash(
        self, skill_name: str, hash_prefix: str,
    ) -> SkillAuditEntry | None:
        """Locate one audit entry whose ``after_hash`` (preferred) or
        ``before_hash`` starts with ``hash_prefix``.

        Accepts a prefix as short as 7 chars so users can paste a short hash
        from ``/skill diff`` output. Returns ``None`` when no match — caller
        produces the "here are valid versions" UX.
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.find_audit_by_hash: entry",
            extra={"_fields": {"skill_name": skill_name, "hash_prefix": hash_prefix[:16]}},
        )
        like = f"{hash_prefix}%"
        rows = await self._db.fetch_all(
            """SELECT audit_id, skill_id, skill_name, source, op, actor,
                      before_hash, after_hash, details, snapshot_json, ts
               FROM skill_audit
               WHERE skill_name = ?
                 AND (after_hash LIKE ? OR before_hash LIKE ?)
               ORDER BY ts DESC LIMIT 1""",
            (skill_name, like, like),
        )
        # 2. DECISION + 4. EXIT
        if not rows:
            log.skills.debug("[skills] store.find_audit_by_hash: exit — miss")
            return None
        entry = _row_to_audit(rows[0])
        log.skills.debug(
            "[skills] store.find_audit_by_hash: exit — hit",
            extra={"_fields": {"audit_id": entry.audit_id, "op": entry.op}},
        )
        return entry

    async def recent_audit_for_skill(
        self, skill_name: str, limit: int = 20,
    ) -> list[SkillAuditEntry]:
        """Return the newest ``limit`` audit entries for a named skill."""
        # 1. ENTRY
        log.skills.debug("[skills] store.recent_audit_for_skill: entry",
                  extra={"_fields": {"skill_name": skill_name, "limit": limit}})
        rows = await self._db.fetch_all(
            """SELECT audit_id, skill_id, skill_name, source, op, actor,
                      before_hash, after_hash, details, snapshot_json, ts
               FROM skill_audit WHERE skill_name = ?
               ORDER BY ts DESC LIMIT ?""",
            (skill_name, limit),
        )
        results = [_row_to_audit(r) for r in rows]
        # 4. EXIT
        log.skills.debug("[skills] store.recent_audit_for_skill: exit",
                  extra={"_fields": {"skill_name": skill_name, "n": len(results)}})
        return results


def _row_to_skill(row: dict[str, object]) -> Skill:
    parent_traces_raw = str(row.get("parent_traces") or "[]")
    try:
        parent_traces = json.loads(parent_traces_raw)
        if not isinstance(parent_traces, list):
            parent_traces = []
    except json.JSONDecodeError:
        parent_traces = []
    manifest_raw = str(row.get("manifest_json") or "{}")
    try:
        manifest_dict = json.loads(manifest_raw)
        if not isinstance(manifest_dict, dict):
            manifest_dict = {}
    except json.JSONDecodeError:
        manifest_dict = {}
    emb_raw = row.get("embedding")
    embedding = None
    if isinstance(emb_raw, bytes | bytearray | memoryview):
        try:
            embedding = unpack_embedding(bytes(emb_raw))
        except Exception as exc:
            log.skills.warning(
                "[skills] store._row_to_skill: embedding unpack failed, dropping embedding",
                exc_info=exc,
            )
            embedding = None
    sr_raw = row.get("success_rate")
    return Skill(
        skill_id=int(str(row["skill_id"])),
        name=str(row["name"]),
        source=str(row["source"]),  # type: ignore[arg-type]
        path=str(row["path"]),
        description=str(row.get("description", "")),
        when_to_use=str(row.get("when_to_use", "")),
        version=str(row.get("version", "0.0.0")),
        enabled=bool(row.get("enabled", 1)),
        success_rate=float(str(sr_raw)) if sr_raw is not None else None,
        n_executions=int(str(row.get("n_executions", 0))),
        parent_traces=list(parent_traces),
        embedding=embedding,
        embedding_model=str(row["embedding_model"]) if row.get("embedding_model") else None,
        body_text=str(row.get("body_text", "")),
        manifest_json=manifest_dict,
        loaded_at=float(str(row["loaded_at"])),
        updated_at=float(str(row["updated_at"])),
    )


def _row_to_audit(row: dict[str, object]) -> SkillAuditEntry:
    details_raw = str(row.get("details") or "{}")
    try:
        details = json.loads(details_raw)
        if not isinstance(details, dict):
            details = {}
    except json.JSONDecodeError:
        details = {}
    snapshot_raw = str(row.get("snapshot_json") or "{}")
    try:
        parsed = json.loads(snapshot_raw)
    except json.JSONDecodeError:
        parsed = {}
    snapshot = (
        {str(k): str(v) for k, v in parsed.items()}
        if isinstance(parsed, dict) else {}
    )
    skill_id_raw = row.get("skill_id")
    return SkillAuditEntry(
        audit_id=int(str(row["audit_id"])),
        skill_id=int(str(skill_id_raw)) if skill_id_raw is not None else None,
        skill_name=str(row["skill_name"]),
        source=str(row["source"]),  # type: ignore[arg-type]
        op=str(row["op"]),
        actor=str(row["actor"]),
        before_hash=str(row["before_hash"]) if row.get("before_hash") else None,
        after_hash=str(row["after_hash"]) if row.get("after_hash") else None,
        details=details,
        snapshot=snapshot,
        ts=float(str(row["ts"])),
    )
