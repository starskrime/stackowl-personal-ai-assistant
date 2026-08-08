"""SkillIndexStore — SQLite cache + audit log over the skills/ workspace.

Files are source of truth (one directory per skill under
``~/.stackowl/skills/<source>/<name>/``). This store caches the
parsed manifest + body + embedding for fast retrieval, plus a ``skill_audit``
forensic trail so ``/skill diff`` and ``/skill restore`` can show every agent
edit. Mirrors :class:`TaskOutcomeStore` (Commit 1) and :class:`ReflectionStore`
(Commit 2).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import pack_embedding, unpack_embedding
from stackowl.skills.lifecycle import _CurationRow
from stackowl.skills.manifest import SkillSource
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

if TYPE_CHECKING:
    from stackowl.skills.loader import LoadedSkill


# LAT.4 — boot-time embedding back-fill (SkillsAssembly._embed_missing, up to
# ~300 skills) writes in bounded chunks of this size instead of one
# execute()-per-row autocommit — the exact "~24-40s catalog scan writing
# ~300 rows" starvation case pool.py:27-38 documents. Bounded (not
# unbounded) so one boot-time scan can never itself hold the single-writer
# lock for a long unbroken span.
_EMBED_CHUNK_SIZE = 100


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
    tool_names: tuple[str, ...]
    body_text: str
    manifest_json: dict[str, object]
    loaded_at: float
    updated_at: float
    lessons_published_hash: str | None = None
    #: ADR-19 lifecycle. Defaults to 'active' so any construction
    #: path that predates the column behaves exactly as before.
    lifecycle_state: str = "active"
    #: Which authoring-standard version this skill was last migrated to. 0 means
    #: "predates the standard" (D10.2 R6Q24, migration 0111).
    standard_version: int = 0


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


_SUMMARY_SANITIZER_VERSION = "1"


def _summary_hash(loaded: LoadedSkill, override: str | None) -> str:
    """Stable content hash for a skill's body + override text.

    Used by the author write (T5) and the back-fill path (T6) so both produce
    the same hash for identical content and the back-fill can skip already-current rows.
    """
    h = hashlib.sha256()
    h.update(loaded.body.encode("utf-8"))
    h.update(b"\x00")
    h.update((override or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(loaded.manifest.source.encode("utf-8"))
    h.update(b"\x00")
    h.update(_SUMMARY_SANITIZER_VERSION.encode("utf-8"))
    return h.hexdigest()


_UPSERT_SQL = """
INSERT INTO skills (
    name, source, path, description, when_to_use, version, enabled,
    success_rate, n_executions, parent_traces, embedding, embedding_model,
    manifest_json, body_text, loaded_at, updated_at, owner_id, tool_names
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(owner_id, source, name) DO UPDATE SET
    path = excluded.path,
    description = excluded.description,
    when_to_use = excluded.when_to_use,
    version = excluded.version,
    enabled = excluded.enabled,
    parent_traces = excluded.parent_traces,
    manifest_json = excluded.manifest_json,
    body_text = excluded.body_text,
    updated_at = excluded.updated_at,
    tool_names = excluded.tool_names
"""

_SELECT_FIELDS = """
    skill_id, name, source, path, description, when_to_use, version, enabled,
    success_rate, n_executions, parent_traces, embedding, embedding_model,
    manifest_json, body_text, loaded_at, updated_at,
    tool_names, lessons_published_hash,
    lifecycle_state, standard_version
"""

# Same field list, table-prefixed for the hybrid_recall JOIN against skills_fts
# (skills_fts has its own name/description/when_to_use columns, so an
# unprefixed SELECT would be ambiguous once joined).
_SELECT_FIELDS_S = ", ".join(
    f"s.{col.strip()}" for col in _SELECT_FIELDS.replace("\n", " ").split(",") if col.strip()
)

# RRF fusion constant (k in score = sum 1/(k + rank)) — standard choice, needs
# no score-scale normalization between BM25 and cosine.
_RRF_K = 60

_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# ADR-19 — archived skills stay in the database (I3: archival is recoverable and
# nothing is ever deleted) but are not OFFERED. This fragment is the single
# place that decision is expressed, so a new retrieval path cannot forget it.
_NOT_ARCHIVED = "AND lifecycle_state <> 'archived'"

# A stale skill is still reachable, just outranked. Multiplicative on the fused
# RRF score rather than a filter: staleness is evidence about likely usefulness,
# not proof of uselessness, and a stale skill that is genuinely the best match
# for a query should still win against nothing.
_STALE_RANK_PENALTY = 0.5


def _sanitize_fts_query(query: str) -> str:
    """Convert free text into a safe FTS5 MATCH expression.

    Mirrors ``memory/sqlite_helpers._sanitize_fts_query`` (same escaping
    approach — extract Unicode word tokens, join as a quoted disjunction,
    cap at 16 terms). Duplicated locally (rather than imported) to avoid a
    cross-module reach into another package's private helper.
    """
    tokens = _FTS_TOKEN_RE.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:16])


#: stackowl_meta key recording that a real curator pass completed (ADR-19).
_CURATOR_RAN_KEY = "skill_curator_last_run"


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
        tool_names_json = json.dumps(list(loaded.tool_names), separators=(",", ":"))
        await self._db.execute(
            _UPSERT_SQL,
            (
                m.name, m.source, str(loaded.path), m.description, m.when_to_use,
                m.version, int(m.enabled), m.success_rate, m.n_executions,
                parent_traces, None, m.embedding_model, manifest_json,
                loaded.body, now, now, self._owner_id, tool_names_json,
            ),
        )
        # Find the row id for the row we just upserted (caller may need it).
        rows = await self._db.fetch_all(
            "SELECT skill_id FROM skills WHERE owner_id = ? AND source = ? AND name = ?",
            (self._owner_id, m.source, m.name),
        )
        skill_id = int(str(rows[0]["skill_id"])) if rows else -1
        # Keep skills_fts in sync — name/description/when_to_use may have changed.
        if skill_id != -1:
            await self._sync_fts(skill_id)
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
            f"WHERE owner_id = ? AND enabled = 1 {_NOT_ARCHIVED} ORDER BY source, name",
            (self._owner_id,),
        )
        results = [_row_to_skill(r) for r in rows]
        # 4. EXIT
        log.skills.debug("[skills] store.list_enabled: exit",
                  extra={"_fields": {"count": len(results)}})
        return results

    async def index_by_source_name(self) -> dict[tuple[str, str], Skill]:
        """Return every skill for this owner in one query, keyed by (source, name).

        Mirrors :meth:`list_enabled`'s single-query shape. Unlike
        :meth:`get_many_by_name`, duplicate names across sources are NOT
        collapsed — a builtin and a learned skill can legitimately share a
        name, and callers (SkillsAssembly's boot back-fill passes) need to
        track each row independently. Snapshot semantics: reflects the table
        at call time only: callers that need to observe writes made by an
        earlier pass in the same boot must take a fresh snapshot per pass.
        """
        # 1. ENTRY
        log.skills.debug("[skills] store.index_by_source_name: entry")
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM skills WHERE owner_id = ?",
            (self._owner_id,),
        )
        index: dict[tuple[str, str], Skill] = {
            (str(sk.source), sk.name): sk for sk in (_row_to_skill(r) for r in rows)
        }
        # 4. EXIT
        log.skills.debug(
            "[skills] store.index_by_source_name: exit",
            extra={"_fields": {"count": len(index)}},
        )
        return index

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
        # A disabled skill re-enabled via /skill enable would otherwise be
        # keyword-unreachable in skills_fts until the next boot re-scan
        # (loader.py upserts all skills) — sync now so hybrid_recall's
        # keyword tier sees it immediately.
        await self._sync_fts(skill_id)
        # D01.4 — the skills catalogue is the largest movable part of the frozen
        # prompt (~4153 chars), so toggling a skill makes every frozen prompt
        # stale. Invalidated HERE, next to the write, rather than in the /skill
        # command: a future writer that reaches the catalogue by another route
        # then still invalidates. Not owl-scoped — the catalogue is a
        # machine-wide fact, which is why invalidate_all and not invalidate_owl.
        await self._invalidate_prompts(cause="skill_enabled" if enabled else "skill_disabled")
        # 4. EXIT
        log.skills.info("[skills] store.set_enabled: stored",
                 extra={"_fields": {"skill_id": skill_id, "enabled": enabled}})

    async def _invalidate_prompts(self, *, cause: str) -> None:
        """Clear every frozen prompt after a catalogue change (D01.4).

        Fail-open: the catalogue write has already committed, so a failure here
        must cost a stale prompt until rollover, never the toggle the user asked
        for. ``invalidate_all`` already logs and swallows its own errors.
        """
        from stackowl.infra import presented_tools
        from stackowl.sessions.prompt_store import SessionPromptStore

        # D05.2 — a skill toggle moves more than the prompt. An owl's presented
        # PINS are its base tools ∪ its owned skills' tool names, so enabling a
        # skill adds tools that the session-scoped tool memo would otherwise keep
        # withholding until rollover. Machine-wide, like the catalogue itself.
        presented_tools.clear()
        await SessionPromptStore(self._db).invalidate_all(cause=cause)

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

    async def set_embeddings_batch(
        self, items: Sequence[tuple[int, list[float] | None, str | None]],
    ) -> None:
        """Batch-write embeddings for many skills in bounded chunked
        transactions (LAT.4). Used by SkillsAssembly's boot-time catalog scan
        (up to ~300 skills) in place of one ``set_embedding()``
        execute()-per-row autocommit per skill — replaces N commits with
        ``ceil(N / _EMBED_CHUNK_SIZE)`` commits. A write failure inside a
        chunk rolls back only that chunk (pool.transaction() semantics); a
        crash mid-chunk loses at most that chunk's writes, not the whole
        scan — acceptable under WAL's synchronous=NORMAL (unchanged by this
        story). A no-op on an empty ``items``.
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.set_embeddings_batch: entry",
            extra={"_fields": {"n_items": len(items)}},
        )
        if not items:
            return
        now = time.time()
        written = 0
        for start in range(0, len(items), _EMBED_CHUNK_SIZE):
            chunk = items[start:start + _EMBED_CHUNK_SIZE]
            try:
                async with self._db.transaction() as tx:
                    for skill_id, embedding, model in chunk:
                        blob = pack_embedding(embedding) if embedding else None
                        await tx.execute(
                            "UPDATE skills SET embedding = ?, embedding_model = ?, updated_at = ? "
                            "WHERE skill_id = ? AND owner_id = ?",
                            (blob, model, now, skill_id, self._owner_id),
                        )
            except Exception as exc:  # B5 — one bad chunk must not lose other chunks
                log.skills.warning(
                    "[skills] store.set_embeddings_batch: chunk failed — rolled back, skipping",
                    exc_info=exc,
                    extra={"_fields": {"chunk_size": len(chunk), "chunk_start": start}},
                )
                continue
            written += len(chunk)
            log.skills.debug(
                "[skills] store.set_embeddings_batch: chunk committed",
                extra={"_fields": {"chunk_size": len(chunk), "written_so_far": written}},
            )
        # 4. EXIT
        log.skills.info(
            "[skills] store.set_embeddings_batch: exit",
            extra={"_fields": {"total": len(items)}},
        )

    # ``set_summary`` REMOVED in D09.3 slice 5 along with the three columns it
    # wrote. ``description`` (capped at 60 chars) and ``when_to_use`` (required,
    # rich) now carry what the generated summary approximated, and two writers to
    # one fact is how fields drift. See migration 0110 for the measurement.

    async def set_lessons_hash(self, skill_id: int, content_hash: str) -> None:
        """Record the content hash last published into the LessonsIndex.

        A content-hash gate of the same shape the removed ``set_summary`` used: lets
        ``_publish_to_lessons`` skip the (locally-computed, uncached-by-default)
        re-embed for a skill whose content hasn't changed since the last boot.
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.set_lessons_hash: entry",
            extra={"_fields": {"skill_id": skill_id}},
        )
        await self._db.execute(
            "UPDATE skills SET lessons_published_hash = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (content_hash, time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.debug(
            "[skills] store.set_lessons_hash: exit",
            extra={"_fields": {"skill_id": skill_id}},
        )

    async def increment_n_executions(self, skill_id: int) -> None:
        """Bump n_executions by 1. Called when the agent uses a skill.

        ADR-19 — also stamps ``last_used_at`` and REVIVES the skill. Two things
        matter here:

        * ``updated_at`` cannot serve as a use-clock: re-scans and metadata
          upserts stamp it too, so a skill nobody has run looks freshly used
          after any rescan. The curator would then never retire anything.
        * Using an archived skill brings it straight back to ``active``. This is
          what makes archival safe to be decisive about — a wrong retirement
          costs one ranking penalty, not a lost capability.
        """
        # 1. ENTRY
        log.skills.debug("[skills] store.increment_n_executions: entry",
                  extra={"_fields": {"skill_id": skill_id}})
        now = time.time()
        await self._db.execute(
            "UPDATE skills SET n_executions = n_executions + 1, updated_at = ?, "
            "last_used_at = ?, "
            "state_changed_at = CASE WHEN lifecycle_state = 'active' "
            "                        THEN state_changed_at ELSE ? END, "
            "lifecycle_state = 'active' "
            "WHERE skill_id = ? AND owner_id = ?",
            (now, now, now, skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.debug("[skills] store.increment_n_executions: exit",
                  extra={"_fields": {"skill_id": skill_id}})

    # ------------------------------------------------------------------ ADR-19 lifecycle

    async def rows_for_curation(self) -> list[_CurationRow]:
        """The whole catalog, projected to what a decay decision needs.

        EVERY SOURCE, not just learned (D09.3 R2Q6). Built-ins decay on the same
        windows with pinning as the protection — the operator's explicit
        decision, reversing the original learned-only scope. 9 of 14 built-ins
        have never run; excluding them meant the shipped shelf could only ever
        grow.

        ``success_rate`` comes along because the quality trigger absorbed from
        the synthesizer (X11) needs the measured rate, and a second query per
        pass to fetch it would let the two reads disagree.
        """
        log.skills.debug("[skills] store.rows_for_curation: entry")
        rows = await self._db.fetch_all(
            "SELECT skill_id, name, lifecycle_state, pinned, n_executions, "
            "       last_used_at, loaded_at, success_rate, source "
            "FROM skills WHERE owner_id = ?",
            (self._owner_id,),
        )
        out = [
            _CurationRow(
                skill_id=int(str(r["skill_id"])),
                name=str(r["name"]),
                lifecycle_state=str(r["lifecycle_state"] or "active"),
                pinned=bool(r["pinned"]),
                n_executions=int(str(r["n_executions"] or 0)),
                last_used_at=float(str(r["last_used_at"])) if r["last_used_at"] else None,
                loaded_at=float(str(r["loaded_at"])) if r["loaded_at"] else None,
                # None means "no verdict yet" and must never read as failing.
                success_rate=(
                    float(str(r["success_rate"])) if r["success_rate"] is not None else None
                ),
                source=str(r["source"] or "learned"),
            )
            for r in rows
        ]
        log.skills.debug(
            "[skills] store.rows_for_curation: exit",
            extra={"_fields": {"rows": len(out)}},
        )
        return out

    async def set_lifecycle_state(self, skill_id: int, state: str, now: float) -> None:
        """Move one skill's lifecycle state. NEVER deletes (ADR-19 I3)."""
        log.skills.debug(
            "[skills] store.set_lifecycle_state: entry",
            extra={"_fields": {"skill_id": skill_id, "state": state}},
        )
        await self._db.execute(
            "UPDATE skills SET lifecycle_state = ?, state_changed_at = ? "
            "WHERE skill_id = ? AND owner_id = ? AND pinned = 0",
            (state, now, skill_id, self._owner_id),
        )
        log.skills.debug(
            "[skills] store.set_lifecycle_state: exit",
            extra={"_fields": {"skill_id": skill_id, "state": state}},
        )

    async def set_pinned(self, skill_id: int, pinned: bool) -> None:
        """The human veto (ADR-19 I4). Pinning also revives, because pinning an
        archived skill can only mean "this should not have been retired"."""
        log.skills.info(
            "[skills] store.set_pinned: entry",
            extra={"_fields": {"skill_id": skill_id, "pinned": pinned}},
        )
        now = time.time()
        if pinned:
            await self._db.execute(
                "UPDATE skills SET pinned = 1, lifecycle_state = 'active', "
                "state_changed_at = ? WHERE skill_id = ? AND owner_id = ?",
                (now, skill_id, self._owner_id),
            )
        else:
            await self._db.execute(
                "UPDATE skills SET pinned = 0 WHERE skill_id = ? AND owner_id = ?",
                (skill_id, self._owner_id),
            )
        log.skills.info(
            "[skills] store.set_pinned: exit",
            extra={"_fields": {"skill_id": skill_id, "pinned": pinned}},
        )

    async def curator_has_run(self) -> bool:
        """Whether a real (non-dry) curator pass has ever completed.

        Drives the first-pass deferral: on a catalog that has never been
        curated every unused skill is simultaneously eligible, so an immediate
        first pass would be the largest change the curator ever makes, taken
        before anyone could pin anything.
        """
        rows = await self._db.fetch_all(
            "SELECT value FROM stackowl_meta WHERE key = ?", (_CURATOR_RAN_KEY,),
        )
        return bool(rows)

    async def mark_curator_ran(self, now: float) -> None:
        """Record that a pass completed, so the next one acts."""
        await self._db.execute(
            "INSERT INTO stackowl_meta (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (_CURATOR_RAN_KEY, str(now), now),
        )

    async def lifecycle_counts(self) -> dict[str, int]:
        """``{state: count}`` over learned skills — for reporting and tests."""
        rows = await self._db.fetch_all(
            "SELECT lifecycle_state AS s, COUNT(*) AS n FROM skills "
            "WHERE owner_id = ? AND source = 'learned' GROUP BY lifecycle_state",
            (self._owner_id,),
        )
        return {str(r["s"] or "active"): int(str(r["n"])) for r in rows}

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

    async def set_n_executions(self, skill_id: int, n: int) -> None:
        """Overwrite the execution count outright.

        Exists for ONE caller: consolidation, where the survivor of a ``-N``
        family inherits the family's summed executions. ``increment_n_executions``
        cannot express that, and without it consolidating a family would destroy
        the usage history that justified keeping the survivor — the curator
        would then archive it for looking unused, days after we merged it.

        Deliberately does NOT touch ``last_used_at`` or ``lifecycle_state``: this
        is a bookkeeping correction, not a use. Claiming a use here would revive
        an archived survivor on a merge nobody ran.
        """
        # 1. ENTRY
        log.skills.debug("[skills] store.set_n_executions: entry",
                  extra={"_fields": {"skill_id": skill_id, "n": n}})
        await self._db.execute(
            "UPDATE skills SET n_executions = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (max(0, n), time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.info("[skills] store.set_n_executions: stored",
                 extra={"_fields": {"skill_id": skill_id, "n": n}})

    async def set_standard_version(self, skill_id: int, version: int) -> None:
        """Record that this skill now meets authoring standard ``version``.

        Written ONLY after the rewritten file has passed the validator and been
        stored — recording conformance we have not verified would make the
        migrator skip exactly the skills it failed on, which is the one bug that
        would be invisible in its own report.
        """
        # 1. ENTRY
        log.skills.debug("[skills] store.set_standard_version: entry",
                  extra={"_fields": {"skill_id": skill_id, "version": version}})
        await self._db.execute(
            "UPDATE skills SET standard_version = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (version, time.time(), skill_id, self._owner_id),
        )
        # 4. EXIT
        log.skills.info("[skills] store.set_standard_version: stored",
                 extra={"_fields": {"skill_id": skill_id, "version": version}})

    async def rename(self, skill_id: int, name: str, path: str) -> None:
        """Move a skill onto a new name and path, keeping FTS in step.

        Both fields together, in one call, because they are one fact: the
        directory IS the name. Updating the row without the path leaves the
        loader unable to find the body it just renamed.
        """
        # 1. ENTRY
        log.skills.debug("[skills] store.rename: entry",
                  extra={"_fields": {"skill_id": skill_id, "name": name}})
        await self._db.execute(
            "UPDATE skills SET name = ?, path = ?, updated_at = ? "
            "WHERE skill_id = ? AND owner_id = ?",
            (name, path, time.time(), skill_id, self._owner_id),
        )
        # skills_fts indexes the name, so a rename that skipped this would leave
        # the skill keyword-reachable only under the name it no longer has.
        await self._sync_fts(skill_id)
        # 4. EXIT
        log.skills.info("[skills] store.rename: renamed",
                 extra={"_fields": {"skill_id": skill_id, "name": name}})

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
            f"WHERE owner_id = ? AND enabled = 1 {_NOT_ARCHIVED} AND embedding IS NOT NULL",
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

    async def hybrid_recall(
        self,
        query_text: str,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[tuple[Skill, float]]:
        """Hybrid BM25 (skills_fts) + cosine (:meth:`semantic_recall`) recall,
        fused via Reciprocal Rank Fusion.

        Runs both passes independently, then fuses their rank lists:
        ``score(skill) = sum over passes where it appears of 1/(k + rank)``
        (``k`` = :data:`_RRF_K`). No score-scale normalization is needed —
        that's the point of RRF over a hand-tuned weighted sum, since BM25
        and cosine scores aren't on the same scale. Returns the top-``limit``
        ``(Skill, fused_score)`` pairs, descending.
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.hybrid_recall: entry",
            extra={"_fields": {"query_len": len(query_text), "limit": limit}},
        )
        # 3. STEP — cosine pass (reuses semantic_recall's own scoring, not duplicated)
        semantic_hits = await self.semantic_recall(list(query_embedding), limit=limit)
        # 3. STEP — keyword pass (FTS5 BM25)
        keyword_hits = await self._fts_search(query_text, limit=limit)
        # 3. STEP — RRF fuse
        fused: dict[int, float] = {}
        by_id: dict[int, Skill] = {}
        for rank, (sk, _score) in enumerate(semantic_hits):
            fused[sk.skill_id] = fused.get(sk.skill_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            by_id[sk.skill_id] = sk
        for rank, sk in enumerate(keyword_hits):
            fused[sk.skill_id] = fused.get(sk.skill_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            by_id[sk.skill_id] = sk
        # ADR-19 — outrank stale skills rather than hiding them. Applied AFTER
        # fusion so it never changes the relative order within a pass, only the
        # standing of a skill nothing has used in a month.
        for sid, sk in by_id.items():
            if sk.lifecycle_state == "stale":
                fused[sid] *= _STALE_RANK_PENALTY
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        results = [(by_id[sid], score) for sid, score in ranked]
        # 4. EXIT
        log.skills.debug(
            "[skills] store.hybrid_recall: exit",
            extra={"_fields": {
                "semantic_hits": len(semantic_hits), "keyword_hits": len(keyword_hits),
                "fused": len(results),
            }},
        )
        return results

    async def _fts_search(self, query_text: str, *, limit: int) -> list[Skill]:
        """FTS5 BM25 keyword pass over ``skills_fts``, owner-scoped +
        enabled-only + not-archived, ordered by ``bm25(skills_fts)`` — mirrors
        ``memory/sqlite_helpers.fts_recall``'s JOIN + ``ORDER BY bm25(...)``
        shape. Fails soft (``[]``) on a parse error the sanitizer missed.

        THE ARCHIVED GUARD IS LOAD-BEARING and was missing until D09.3 slice 3.
        ``list_enabled`` and :meth:`semantic_recall` both carried it; this leg
        did not, so an archived skill vanished from two of the three read paths
        and kept competing on the third. A retirement that only half applies is
        worse than none — it costs the write and delivers no ranking relief.
        """
        fts_query = _sanitize_fts_query(query_text)
        if not fts_query:
            log.skills.debug("[skills] store._fts_search: exit — empty query after sanitize")
            return []
        try:
            rows = await self._db.fetch_all(
                f"""SELECT {_SELECT_FIELDS_S} FROM skills_fts fts
                    JOIN skills s ON s.skill_id = fts.rowid
                    WHERE skills_fts MATCH ? AND s.owner_id = ? AND s.enabled = 1
                      AND s.lifecycle_state <> 'archived'
                    ORDER BY bm25(skills_fts)
                    LIMIT ?""",
                (fts_query, self._owner_id, limit),
            )
        except Exception as exc:
            # FTS5 still rejected the sanitized query (rare) — fail soft.
            log.skills.warning(
                "[skills] store._fts_search: FTS5 query failed — returning empty",
                exc_info=exc, extra={"_fields": {"query_len": len(query_text)}},
            )
            return []
        results = [_row_to_skill(r) for r in rows]
        # 4. EXIT
        log.skills.debug(
            "[skills] store._fts_search: exit", extra={"_fields": {"n_results": len(results)}},
        )
        return results

    _SOURCE_PRIORITY: dict[str, int] = {"user": 0, "learned": 1, "installed": 2, "builtin": 3}

    async def get_many_by_name(self, names: tuple[str, ...]) -> list[Skill]:
        """Resolve bare skill names -> Skills (one owner-scoped query).

        When a name exists under multiple sources, pick by _SOURCE_PRIORITY
        (lower = higher priority). Request order preserved; unknown names
        dropped. Reused by assemble (summaries) + execute (tool_names) in T9/T11.
        """
        # 1. ENTRY
        log.skills.debug(
            "[skills] store.get_many_by_name: entry",
            extra={"_fields": {"n": len(names)}},
        )
        if not names:
            return []
        # 2. DECISION — single IN query; dedup by source priority in Python
        placeholders = ",".join("?" for _ in names)
        # 3. STEP — fetch all matching rows across sources in one query
        rows = await self._db.fetch_all(
            f"SELECT {_SELECT_FIELDS} FROM skills WHERE owner_id = ? AND name IN ({placeholders})",
            (self._owner_id, *names),
        )
        by_name: dict[str, Skill] = {}
        for r in rows:
            sk = _row_to_skill(r)
            cur = by_name.get(sk.name)
            if cur is None or (
                self._SOURCE_PRIORITY.get(sk.source, 9)
                < self._SOURCE_PRIORITY.get(cur.source, 9)
            ):
                by_name[sk.name] = sk
        result = [by_name[n] for n in names if n in by_name]
        # 4. EXIT
        log.skills.debug(
            "[skills] store.get_many_by_name: exit",
            extra={"_fields": {"resolved": len(result)}},
        )
        return result

    async def delete(self, skill_id: int) -> None:
        """Remove the index row (+ its skills_fts row). File system deletion
        is the caller's job. Base + FTS deletes run in one transaction
        (mirrors sqlite_bridge.delete) so a crash mid-op cannot leave the
        two divergent."""
        # 1. ENTRY
        log.skills.debug("[skills] store.delete: entry",
                  extra={"_fields": {"skill_id": skill_id}})
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM skills_fts WHERE rowid = ?", (skill_id,),
            )
            await tx.execute(
                "DELETE FROM skills WHERE skill_id = ? AND owner_id = ?",
                (skill_id, self._owner_id),
            )
        # 4. EXIT
        log.skills.info("[skills] store.delete: deleted",
                 extra={"_fields": {"skill_id": skill_id}})

    async def _sync_fts(self, skill_id: int) -> None:
        """Re-sync ``skills_fts`` for one ``skill_id`` from the current
        ``skills`` row. FTS5 has no UPSERT, so this deletes then re-inserts
        the row atomically — mirrors ``committed_facts_fts``'s
        application-layer sync (``sqlite_bridge.py``). A no-op if the skill
        row is gone (e.g. deleted concurrently)."""
        rows = await self._db.fetch_all(
            "SELECT name, description, when_to_use FROM skills "
            "WHERE skill_id = ? AND owner_id = ?",
            (skill_id, self._owner_id),
        )
        if not rows:
            return
        r = rows[0]
        async with self._db.transaction() as tx:
            await tx.execute("DELETE FROM skills_fts WHERE rowid = ?", (skill_id,))
            await tx.execute(
                "INSERT INTO skills_fts (rowid, name, description, when_to_use) "
                "VALUES (?, ?, ?, ?)",
                (
                    skill_id, str(r["name"]), str(r["description"]),
                    str(r["when_to_use"] or ""),
                ),
            )

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
        tool_names=tuple(json.loads(str(row.get("tool_names") or "[]"))),
        body_text=str(row.get("body_text", "")),
        manifest_json=manifest_dict,
        loaded_at=float(str(row["loaded_at"])),
        updated_at=float(str(row["updated_at"])),
        lessons_published_hash=(
            str(row["lessons_published_hash"]) if row.get("lessons_published_hash") is not None else None
        ),
        # ADR-19. `or "active"` covers both a NULL column and a caller that
        # selected an older field list — a missing lifecycle must never read as
        # archived, which would silently hide a working skill.
        lifecycle_state=str(row.get("lifecycle_state") or "active"),
        # 0 = predates the standard. A missing column must read as UNMIGRATED,
        # never as current — the opposite default would declare the backlog
        # migrated by fiat and leave the migrator nothing to find.
        standard_version=int(str(row.get("standard_version") or 0)),
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
