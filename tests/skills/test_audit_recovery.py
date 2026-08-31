"""Deleted skills can be reconstructed from the audit trail's own snapshots.

EARNED 2026-08-31. On 2026-08-30 a purge removed 151 learned skills; the audit
row it wrote names a dump and a pre-purge database backup, and neither exists
anywhere on the box. I reported the loss as irreversible and recommended
accepting it. That was wrong: ``skill_audit.snapshot_json`` is a working
row-snapshot facility holding the full body of 128 skills that no longer exist
in the ``skills`` table. The purge's own 151 rows are empty only because it
bypassed ``SkillStore`` with hand-written SQL.

This makes that recovery path reachable, before an age-based retention rule
prunes the rows and the loss becomes real after all.

Non-destructive by construction: it reads the audit trail and writes files. It
never touches ``skills``, and it does not re-register anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.audit_recovery import SkillAuditRecovery

_INSERT = """INSERT INTO skill_audit
    (skill_name, source, op, actor, details, ts, snapshot_json)
    VALUES (?, 'learned', ?, 'test', '{}', ?, ?)"""


async def _audit(db: DbPool, name: str, op: str, ts: float, snapshot: dict[str, str] | None) -> None:
    await db.execute(_INSERT, (name, op, ts, json.dumps(snapshot) if snapshot is not None else "{}"))


# =========================================================================== #
# 1. What is recoverable
# =========================================================================== #


@pytest.mark.asyncio
async def test_a_deleted_skill_with_a_snapshot_is_recoverable(tmp_db: DbPool) -> None:
    await _audit(tmp_db, "gone-but-saved", "create", 1000.0, {"SKILL.md": "# body"})
    found = await SkillAuditRecovery(tmp_db).recoverable()
    assert [f.skill_name for f in found] == ["gone-but-saved"]


@pytest.mark.asyncio
async def test_the_purges_empty_rows_are_not_offered_as_recoverable(tmp_db: DbPool) -> None:
    """151 rows carry '{}'. Offering them would promise a body that is not there."""
    await _audit(tmp_db, "no-body", "purge_all", 1000.0, None)
    await _audit(tmp_db, "also-none", "delete", 1001.0, {})
    assert await SkillAuditRecovery(tmp_db).recoverable() == []


@pytest.mark.asyncio
async def test_a_skill_that_still_exists_is_not_offered(tmp_db: DbPool) -> None:
    """Recovery is for what was LOST. A live skill would be overwritten by its own past."""
    # loaded_at/updated_at are NOT NULL with no default — a fixture without them
    # cannot insert a skill at all, so it could never exercise the live-skill branch.
    await tmp_db.execute(
        "INSERT INTO skills (name, source, path, description, when_to_use, body_text, "
        "loaded_at, updated_at) "
        "VALUES ('still-here', 'learned', '/x', 'd', 'w', 'b', 1000.0, 1000.0)"
    )
    await _audit(tmp_db, "still-here", "create", 1000.0, {"SKILL.md": "# old body"})
    assert await SkillAuditRecovery(tmp_db).recoverable() == []


@pytest.mark.asyncio
async def test_the_newest_snapshot_wins(tmp_db: DbPool) -> None:
    """Two names in the live audit carry several snapshots; the last edit is the skill."""
    await _audit(tmp_db, "edited-twice", "create", 1000.0, {"SKILL.md": "# first"})
    await _audit(tmp_db, "edited-twice", "update", 2000.0, {"SKILL.md": "# latest"})
    found = await SkillAuditRecovery(tmp_db).recoverable()
    assert len(found) == 1
    assert found[0].files["SKILL.md"] == "# latest"


# =========================================================================== #
# 2. Writing it out
# =========================================================================== #


@pytest.mark.asyncio
async def test_export_writes_each_skill_under_its_own_name(tmp_db: DbPool, tmp_path: Path) -> None:
    await _audit(tmp_db, "alpha", "create", 1000.0, {"SKILL.md": "# alpha"})
    await _audit(tmp_db, "beta", "create", 1001.0, {"config.json": "{}"})
    dest = tmp_path / "archive"

    written = await SkillAuditRecovery(tmp_db).export(dest)

    assert (dest / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "# alpha"
    assert (dest / "beta" / "config.json").read_text(encoding="utf-8") == "{}"
    assert written == 2


@pytest.mark.asyncio
async def test_export_writes_a_manifest_and_the_manifest_is_true(
    tmp_db: DbPool, tmp_path: Path
) -> None:
    """This week's lesson, applied to my own output.

    An audit row named two backups that were never on disk. A manifest that
    lists a file it did not write is the same defect. Every path it names is
    observed before the manifest is written.
    """
    await _audit(tmp_db, "alpha", "create", 1000.0, {"SKILL.md": "# alpha"})
    dest = tmp_path / "archive"

    await SkillAuditRecovery(tmp_db).export(dest)

    manifest = json.loads((dest / "recovery-manifest.json").read_text(encoding="utf-8"))
    assert manifest["skills"]
    for entry in manifest["skills"]:
        for rel in entry["files"]:
            assert (dest / rel).is_file(), f"manifest names {rel}, which is not there"


@pytest.mark.asyncio
async def test_nothing_is_written_outside_the_destination(tmp_db: DbPool, tmp_path: Path) -> None:
    """The filename inside snapshot_json is DATA, not a constant.

    It reaches the database from a model-authored skill. A key like
    ``../../escaped`` must not be able to write outside the archive.
    """
    await _audit(
        tmp_db, "hostile", "create", 1000.0,
        {"../../escaped.md": "should never land", "SKILL.md": "# fine"},
    )
    dest = tmp_path / "archive"

    await SkillAuditRecovery(tmp_db).export(dest)

    assert not (tmp_path.parent / "escaped.md").exists()
    assert not (tmp_path / "escaped.md").exists()
    # The safe file still lands — one bad key does not lose the whole skill.
    assert (dest / "hostile" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_skill_name_cannot_escape_either(tmp_db: DbPool, tmp_path: Path) -> None:
    await _audit(tmp_db, "../../evil", "create", 1000.0, {"SKILL.md": "# no"})
    dest = tmp_path / "archive"
    await SkillAuditRecovery(tmp_db).export(dest)
    assert not (tmp_path.parent / "evil").exists()


# =========================================================================== #
# 3. It reads; it does not change the platform
# =========================================================================== #


@pytest.mark.asyncio
async def test_export_does_not_register_or_delete_anything(tmp_db: DbPool, tmp_path: Path) -> None:
    await _audit(tmp_db, "alpha", "create", 1000.0, {"SKILL.md": "# alpha"})
    before_skills = (await tmp_db.fetch_all("SELECT COUNT(*) AS c FROM skills"))[0]["c"]
    before_audit = (await tmp_db.fetch_all("SELECT COUNT(*) AS c FROM skill_audit"))[0]["c"]

    await SkillAuditRecovery(tmp_db).export(tmp_path / "archive")

    assert (await tmp_db.fetch_all("SELECT COUNT(*) AS c FROM skills"))[0]["c"] == before_skills
    assert (await tmp_db.fetch_all("SELECT COUNT(*) AS c FROM skill_audit"))[0]["c"] == before_audit


@pytest.mark.asyncio
async def test_exporting_twice_is_idempotent(tmp_db: DbPool, tmp_path: Path) -> None:
    await _audit(tmp_db, "alpha", "create", 1000.0, {"SKILL.md": "# alpha"})
    dest = tmp_path / "archive"
    first = await SkillAuditRecovery(tmp_db).export(dest)
    second = await SkillAuditRecovery(tmp_db).export(dest)
    assert first == second == 1
    assert (dest / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "# alpha"


@pytest.mark.asyncio
async def test_an_empty_audit_trail_is_a_clean_noop(tmp_db: DbPool, tmp_path: Path) -> None:
    dest = tmp_path / "archive"
    assert await SkillAuditRecovery(tmp_db).export(dest) == 0
