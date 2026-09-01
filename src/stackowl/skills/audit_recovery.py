"""SkillAuditRecovery — reconstruct deleted skills from the audit trail.

``skill_audit.snapshot_json`` stores the full file contents of a skill at the
moment of an audited change: ``{"SKILL.md": "...", ...}``. It is a real,
populated facility — measured 2026-08-31 on the live database, 132 rows carry a
body and 128 of those name a skill that no longer exists in ``skills``.

WHY THIS EXISTS. The 2026-08-30 purge removed 151 learned skills through
hand-written SQL, bypassing :class:`SkillStore` and therefore its snapshot. The
audit row it wrote names a dump and a pre-purge database backup; neither is
anywhere on the box. The loss was reported as irreversible and it is not — the
skills purged BEFORE that, through the normal path, left their bodies behind.
This makes that path reachable before an age-based retention rule prunes the
rows and the loss becomes real.

READ-ONLY BY CONSTRUCTION. It reads ``skill_audit`` and writes files. It never
writes ``skills``, never re-registers anything, and never deletes an audit row.
Recovering a skill into the live catalogue is a separate, deliberate act.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.verification import verify_artifact

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool

#: OWNER-SCOPED (2026-09-01): ``skills`` is owner-governed, and an unscoped
#: subquery here would treat another principal's live skill as proof that THIS
#: principal's deleted one still exists — suppressing a legitimate recovery.
#: Every row is principal-default today, so this narrows nothing now.
#:
#: Newest snapshot per skill name, for names absent from ``skills``. Ordering is
#: by ``ts`` so the LAST edit wins — an edited-then-deleted skill should come
#: back as its final version, not its first.
_SELECT_SNAPSHOTS = """
SELECT a.skill_name AS skill_name, a.op AS op, a.ts AS ts, a.snapshot_json AS snapshot_json
  FROM skill_audit a
 WHERE a.snapshot_json IS NOT NULL
   AND a.snapshot_json NOT IN ('', '{}')
   AND a.skill_name IS NOT NULL
   AND a.skill_name NOT IN (SELECT name FROM skills WHERE owner_id = ?)
 ORDER BY a.ts ASC
"""


@dataclass(frozen=True)
class RecoverableSkill:
    """One skill that no longer exists and whose body survives in the audit."""

    skill_name: str
    op: str
    ts: float
    files: dict[str, str]

    @property
    def total_bytes(self) -> int:
        return sum(len(v.encode("utf-8")) for v in self.files.values())


def _safe_component(raw: str) -> str | None:
    """Reduce a data-supplied name to a single safe path component.

    The skill name and the keys inside ``snapshot_json`` are DATA — they reach
    the database from model-authored skills, so ``../../escaped.md`` is a shape
    that can occur. Returns ``None`` when nothing safe remains.
    """
    candidate = Path(raw).name.strip()
    if not candidate or candidate in {".", ".."} or candidate.startswith(("/", "\\")):
        return None
    return candidate


class SkillAuditRecovery:
    """Read the audit trail; write the bodies of skills that no longer exist."""

    MANIFEST_NAME = "recovery-manifest.json"

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def recoverable(self) -> list[RecoverableSkill]:
        """Every deleted skill whose body survives, newest snapshot per name."""
        # 1. ENTRY
        log.skills.debug("[skills] audit_recovery.recoverable: entry")
        rows = await self._db.fetch_all(_SELECT_SNAPSHOTS, (DEFAULT_PRINCIPAL_ID,))
        # 2. DECISION — ascending ts means a later row simply overwrites an
        # earlier one for the same name, so the last write is the newest.
        newest: dict[str, RecoverableSkill] = {}
        for row in rows:
            name = str(row["skill_name"])
            try:
                files = json.loads(str(row["snapshot_json"]))
            except (ValueError, TypeError) as exc:
                log.skills.warning(
                    "[skills] audit_recovery: unreadable snapshot — skipping",
                    exc_info=exc,
                    extra={"_fields": {"skill": name}},
                )
                continue
            if not isinstance(files, dict) or not files:
                continue
            newest[name] = RecoverableSkill(
                skill_name=name,
                op=str(row["op"] or ""),
                ts=float(row["ts"] or 0.0),
                files={str(k): str(v) for k, v in files.items()},
            )
        found = sorted(newest.values(), key=lambda s: s.skill_name)
        # 4. EXIT
        log.skills.info(
            "[skills] audit_recovery.recoverable: exit",
            extra={"_fields": {"skills": len(found), "bytes": sum(s.total_bytes for s in found)}},
        )
        return found

    async def export(self, dest: Path) -> int:
        """Write every recoverable skill under ``dest``. Returns how many landed.

        Writes a manifest LAST, and only after observing every file it names —
        a manifest listing something it did not write is the same defect as an
        audit row naming a backup that was never taken.
        """
        # 1. ENTRY
        log.skills.info(
            "[skills] audit_recovery.export: entry", extra={"_fields": {"dest": str(dest)}}
        )
        found = await self.recoverable()
        dest.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        written = 0
        for skill in found:
            folder_name = _safe_component(skill.skill_name)
            if folder_name is None:
                log.skills.warning(
                    "[skills] audit_recovery.export: unusable skill name — skipping",
                    extra={"_fields": {"skill": skill.skill_name}},
                )
                continue
            folder = dest / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            landed: list[str] = []
            for raw_name, content in skill.files.items():
                safe = _safe_component(raw_name)
                if safe is None:
                    # 3. STEP — one hostile key must not lose the whole skill.
                    log.skills.warning(
                        "[skills] audit_recovery.export: refusing an unsafe filename",
                        extra={"_fields": {"skill": skill.skill_name, "name": raw_name}},
                    )
                    continue
                target = folder / safe
                try:
                    target.write_text(content, encoding="utf-8")
                except OSError as exc:
                    log.skills.warning(
                        "[skills] audit_recovery.export: could not write file",
                        exc_info=exc,
                        extra={"_fields": {"skill": skill.skill_name, "file": safe}},
                    )
                    continue
                if verify_artifact(target) is not True:
                    log.skills.warning(
                        "[skills] audit_recovery.export: wrote a file that is not there",
                        extra={"_fields": {"skill": skill.skill_name, "file": safe}},
                    )
                    continue
                landed.append(f"{folder_name}/{safe}")
            if not landed:
                continue
            written += 1
            entries.append({
                "skill_name": skill.skill_name,
                "recovered_from_op": skill.op,
                "audit_ts": skill.ts,
                "files": landed,
            })

        manifest = {
            "created_at": datetime.now(tz=UTC).isoformat(),
            "source": "skill_audit.snapshot_json",
            "note": (
                "Bodies of skills that no longer exist in the skills table. "
                "Nothing here is registered; recovery into the live catalogue is "
                "a separate deliberate act."
            ),
            "skills": entries,
        }
        (dest / self.MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 4. EXIT
        log.skills.info(
            "[skills] audit_recovery.export: exit",
            extra={"_fields": {"dest": str(dest), "skills_written": written}},
        )
        return written
