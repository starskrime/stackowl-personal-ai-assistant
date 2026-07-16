"""Tests for the E4 provenance chokepoints shared by the slash and tool paths.

Skill side (``record_skill_mutation``):
  * create-style mutation → audit row with after_hash + snapshot captured AFTER.
  * delete-style mutation → audit row with snapshot captured BEFORE (so the dir
    can be resurrected) and after_hash None (dir gone).
  * explicit ``snapshot`` + ``before_hash`` overrides (restore path).
  * the mutate callback actually runs.

Memory side (``remember_fact`` / ``forget_fact``):
  * remember tags the configurable source_type (manual default, agent_self for
    the tool) and writes an audit row only when an audit logger is supplied.
  * forget routes the delete through the bridge and audits only when supplied.
  * audit-append failure never aborts the mutation (best-effort).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.memory_helpers import forget_fact, remember_fact
from stackowl.commands.skill_helpers import record_skill_mutation
from stackowl.db.pool import DbPool
from stackowl.memory.models import StagedFact
from stackowl.skills.store import SkillIndexStore


def _write_skill(d: Path, name: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: x\n---\n\nbody\n", encoding="utf-8",
    )


# --- skill chokepoint ------------------------------------------------------

async def test_record_skill_mutation_create_audits_after(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    store = SkillIndexStore(tmp_db)
    skill_dir = tmp_path / "installed" / "demo"
    _write_skill(skill_dir, "demo")

    ran = {"v": False}

    async def _mutate() -> None:
        ran["v"] = True

    before, after = await record_skill_mutation(
        store, skill_name="demo", source="installed", op="create",
        actor="user:local", target_dir=skill_dir, mutate=_mutate,
        snapshot_when="after", details={"path": str(skill_dir)},
    )
    assert ran["v"] is True
    assert after is not None  # tree exists post-mutate
    entries = await store.recent_audit_for_skill("demo")
    assert len(entries) == 1
    e = entries[0]
    assert e.op == "create"
    assert e.actor == "user:local"
    assert e.after_hash == after
    # snapshot captured AFTER → includes SKILL.md content.
    assert "SKILL.md" in e.snapshot


async def test_record_skill_mutation_also_writes_learning_artifact_checkpoint(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """Story 2.3 (AD-2): the additive LearningArtifactStore write happens
    ALONGSIDE skill_audit — both rows exist, neither replaces the other."""
    store = SkillIndexStore(tmp_db)
    skill_dir = tmp_path / "installed" / "both-writes"
    _write_skill(skill_dir, "both-writes")

    async def _mutate() -> None:
        return None

    await record_skill_mutation(
        store, skill_name="both-writes", source="installed", op="create",
        actor="user:local", target_dir=skill_dir, mutate=_mutate,
        snapshot_when="after",
    )

    # skill_audit unchanged — still the read path for /skill restore + /skill diff.
    entries = await store.recent_audit_for_skill("both-writes")
    assert len(entries) == 1
    assert entries[0].op == "create"

    # NEW: an additive learning_artifacts row for the same mutation.
    rows = await tmp_db.fetch_all(
        "SELECT artifact_type, artifact_id, reason FROM learning_artifacts "
        "WHERE artifact_type = 'skill' AND artifact_id = ?",
        ("both-writes",),
    )
    assert len(rows) == 1
    assert rows[0]["reason"] == "create"


async def test_record_skill_mutation_learning_artifact_failure_does_not_abort(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken LearningArtifactStore write must never fail the skill mutation —
    audit_write already succeeded and IS the primary provenance record (Story 2.3)."""
    import stackowl.commands.skill_helpers as sh

    async def _boom(self, *a, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
        raise RuntimeError("learning store down")

    monkeypatch.setattr(sh.LearningArtifactStore, "checkpoint", _boom)

    store = SkillIndexStore(tmp_db)
    skill_dir = tmp_path / "installed" / "resilient"
    _write_skill(skill_dir, "resilient")

    async def _mutate() -> None:
        return None

    before, after = await record_skill_mutation(
        store, skill_name="resilient", source="installed", op="create",
        actor="user:local", target_dir=skill_dir, mutate=_mutate,
        snapshot_when="after",
    )
    assert after is not None  # mutation + audit still succeeded despite the failure
    entries = await store.recent_audit_for_skill("resilient")
    assert len(entries) == 1


async def test_record_skill_mutation_delete_snapshots_before(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    import shutil

    store = SkillIndexStore(tmp_db)
    skill_dir = tmp_path / "installed" / "gone"
    _write_skill(skill_dir, "gone")

    async def _mutate() -> None:
        shutil.rmtree(skill_dir)

    before, after = await record_skill_mutation(
        store, skill_name="gone", source="installed", op="delete",
        actor="user:rm", target_dir=skill_dir, mutate=_mutate,
        snapshot_when="before",
    )
    assert before is not None  # tree existed pre-mutate
    assert after is None  # tree gone post-mutate
    entries = await store.recent_audit_for_skill("gone")
    assert entries[0].op == "delete"
    # snapshot captured BEFORE → still has the content to resurrect.
    assert "SKILL.md" in entries[0].snapshot


async def test_record_skill_mutation_honours_explicit_snapshot_and_before(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    store = SkillIndexStore(tmp_db)
    skill_dir = tmp_path / "installed" / "restored"
    _write_skill(skill_dir, "restored")
    explicit_snap = {"SKILL.md": "EXPLICIT"}

    async def _mutate() -> None:
        return None

    before, _after = await record_skill_mutation(
        store, skill_name="restored", source="installed", op="restore",
        actor="user:restore", target_dir=skill_dir, mutate=_mutate,
        snapshot_when="after", snapshot=explicit_snap, before_hash="deadbeef",
    )
    assert before == "deadbeef"  # override respected
    entries = await store.recent_audit_for_skill("restored")
    assert entries[0].snapshot == explicit_snap  # explicit snapshot, not recaptured
    assert entries[0].before_hash == "deadbeef"


# --- memory chokepoint -----------------------------------------------------

class _FakeBridge:
    def __init__(self) -> None:
        self.staged: list[StagedFact] = []
        self.deleted: list[str] = []

    async def stage(self, fact: StagedFact) -> None:
        self.staged.append(fact)

    async def delete(self, fact_id: str) -> None:
        self.deleted.append(fact_id)


class _FakePromoter:
    def __init__(self) -> None:
        self.promoted: list[str] = []

    async def force_promote(self, fact_id: str) -> None:
        self.promoted.append(fact_id)


class _RecordingAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, object]] = []
        self._fail = fail

    def append(self, *, event_type: str, actor: str, target: str | None,
               details: dict[str, object]) -> None:
        if self._fail:
            raise RuntimeError("audit down")
        self.rows.append({
            "event_type": event_type, "actor": actor,
            "target": target, "details": details,
        })


async def test_remember_fact_defaults_to_manual_no_audit() -> None:
    bridge, promoter = _FakeBridge(), _FakePromoter()
    fact_id = await remember_fact(bridge, promoter, "hello")  # type: ignore[arg-type]
    assert bridge.staged[0].source_type == "manual"
    assert promoter.promoted == [fact_id]


async def test_remember_fact_tags_agent_self_and_audits() -> None:
    bridge, promoter = _FakeBridge(), _FakePromoter()
    audit = _RecordingAudit()
    fact_id = await remember_fact(
        bridge, promoter, "agent wrote this",  # type: ignore[arg-type]
        source_type="agent_self", source_ref="tool", audit=audit,  # type: ignore[arg-type]
        actor="agent_self:memory",
    )
    assert bridge.staged[0].source_type == "agent_self"
    assert len(audit.rows) == 1
    assert audit.rows[0]["event_type"] == "memory.remember"
    assert audit.rows[0]["target"] == fact_id
    assert audit.rows[0]["details"]["source_type"] == "agent_self"


async def test_forget_fact_deletes_and_audits_when_supplied() -> None:
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    await forget_fact(bridge, "fact-123", audit=audit, actor="agent_self:memory")  # type: ignore[arg-type]
    assert bridge.deleted == ["fact-123"]
    assert audit.rows[0]["event_type"] == "memory.forget"
    assert audit.rows[0]["target"] == "fact-123"


async def test_forget_fact_no_audit_when_none() -> None:
    bridge = _FakeBridge()
    await forget_fact(bridge, "fact-x")  # type: ignore[arg-type]
    assert bridge.deleted == ["fact-x"]  # delete still happens, no audit needed


async def test_audit_failure_never_aborts_mutation() -> None:
    bridge, promoter = _FakeBridge(), _FakePromoter()
    audit = _RecordingAudit(fail=True)
    # Must NOT raise even though audit.append raises.
    fact_id = await remember_fact(
        bridge, promoter, "x", audit=audit,  # type: ignore[arg-type]
    )
    assert bridge.staged and promoter.promoted == [fact_id]

    bridge2 = _FakeBridge()
    await forget_fact(bridge2, "y", audit=audit)  # type: ignore[arg-type]
    assert bridge2.deleted == ["y"]
