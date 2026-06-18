"""Tests for Learning Commit 3 sub-phase 3e — snapshot + /skill restore."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.commands.registry import CommandRegistry
from stackowl.commands.skill_command import SkillCommand
from stackowl.commands.skill_helpers import (
    _SNAPSHOT_CAP_BYTES,
    restore_snapshot,
    snapshot_dir,
)
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.registry import ToolRegistry


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="",
        channel="cli", owl_name="system", pipeline_step="start",
    )


def _write_skill_md(d: Path, name: str, *, body: str = "B") -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: x\n---\n\n{body}\n", encoding="utf-8",
    )


@pytest.fixture()
async def wired(tmp_db: DbPool, tmp_path: Path):
    """SkillCommand + tmp workspace + snapshot/restore registry-snapshot trick."""
    root = tmp_path / "ws" / "skills"
    root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    registry = CommandRegistry.instance()
    snapshot_reg = dict(registry._commands)  # type: ignore[attr-defined]
    cmd = SkillCommand.create_and_register(
        store=components.store, loader=components.loader, skills_root=root,
    )
    try:
        yield cmd, root, components.store
    finally:
        registry._commands = snapshot_reg  # type: ignore[attr-defined]


# ---------- snapshot_dir / restore_snapshot helpers ------------------------

def test_snapshot_dir_captures_text_files(tmp_path: Path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")
    (d / "scripts").mkdir()
    (d / "scripts" / "helper.sh").write_text("echo hi")
    snap = snapshot_dir(d)
    assert "SKILL.md" in snap
    assert "scripts/helper.sh" in snap
    assert "body" in snap["SKILL.md"]
    assert snap["scripts/helper.sh"] == "echo hi"


def test_snapshot_dir_skips_binary_extensions(tmp_path: Path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n")
    (d / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n...")
    snap = snapshot_dir(d)
    assert "SKILL.md" in snap
    assert "image.png" not in snap


def test_snapshot_dir_falls_back_to_skill_md_when_oversized(tmp_path: Path) -> None:
    """When the tree exceeds the cap, we keep just SKILL.md per operator vote."""
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")
    # Write a big sidecar that pushes total over the cap.
    big = "x" * (_SNAPSHOT_CAP_BYTES + 1024)
    (d / "references").mkdir()
    (d / "references" / "huge.md").write_text(big)
    snap = snapshot_dir(d)
    assert snap == {"SKILL.md": "---\nname: x\ndescription: y\n---\nbody"}


def test_restore_snapshot_recreates_tree(tmp_path: Path) -> None:
    d = tmp_path / "target"
    snap = {
        "SKILL.md": "---\nname: x\ndescription: y\n---\nthe body",
        "scripts/x.sh": "echo hi",
    }
    restore_snapshot(d, snap)
    assert (d / "SKILL.md").read_text() == snap["SKILL.md"]
    assert (d / "scripts" / "x.sh").read_text() == "echo hi"


def test_restore_snapshot_replaces_existing_content(tmp_path: Path) -> None:
    d = tmp_path / "target"
    d.mkdir()
    (d / "SKILL.md").write_text("OLD")
    (d / "stale.md").write_text("OLD STALE")
    snap = {"SKILL.md": "NEW"}
    restore_snapshot(d, snap)
    assert (d / "SKILL.md").read_text() == "NEW"
    assert not (d / "stale.md").exists()  # restore wipes files not in snapshot


def test_restore_snapshot_rejects_path_traversal(tmp_path: Path) -> None:
    d = tmp_path / "target"
    snap = {"../evil.txt": "pwn", "SKILL.md": "ok"}
    restore_snapshot(d, snap)
    assert (d / "SKILL.md").exists()
    assert not (tmp_path / "evil.txt").exists()


# ---------- audit_write now includes snapshot ------------------------------

async def test_audit_write_stores_and_retrieves_snapshot(tmp_db: DbPool) -> None:
    from stackowl.skills.store import SkillIndexStore

    store = SkillIndexStore(tmp_db)
    await store.audit_write(
        skill_name="foo", source="learned", op="create",
        actor="agent:synthesizer", after_hash="abc1234",
        snapshot={"SKILL.md": "the body"},
    )
    entries = await store.recent_audit_for_skill("foo")
    assert len(entries) == 1
    assert entries[0].snapshot == {"SKILL.md": "the body"}


async def test_find_audit_by_hash_locates_entry(tmp_db: DbPool) -> None:
    from stackowl.skills.store import SkillIndexStore

    store = SkillIndexStore(tmp_db)
    await store.audit_write(
        skill_name="bar", source="installed", op="create",
        actor="user:local", after_hash="deadbeef0001",
        snapshot={"SKILL.md": "x"},
    )
    entry = await store.find_audit_by_hash("bar", "deadbeef")
    assert entry is not None
    assert entry.op == "create"
    assert entry.after_hash == "deadbeef0001"


async def test_find_audit_by_hash_misses_returns_none(tmp_db: DbPool) -> None:
    from stackowl.skills.store import SkillIndexStore

    store = SkillIndexStore(tmp_db)
    assert await store.find_audit_by_hash("nonexistent", "abc") is None


# ---------- /skill restore end-to-end --------------------------------------

async def test_restore_round_trip_from_diff_hash(wired, tmp_path: Path) -> None:
    cmd, root, store = wired
    # Install a skill via /skill add → audit captures snapshot v1
    src = tmp_path / "src" / "demo"
    _write_skill_md(src, name="demo", body="ORIGINAL")
    await cmd.handle(f"add {src}", _state())
    # Manually edit the on-disk SKILL.md (simulating user/agent edit)
    skill_dir = root / "installed" / "demo"
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: x\n---\n\nCHANGED CONTENT\n",
        encoding="utf-8",
    )
    # First create entry has an after_hash listed → grab it
    audit = await store.recent_audit_for_skill("demo")
    create_entry = next(e for e in audit if e.op == "create")
    target_hash = create_entry.after_hash[:12]
    # Restore
    out = await cmd.handle(f"restore demo --version {target_hash}", _state())
    assert "✓ Restored" in out
    # Disk content is now ORIGINAL again
    assert "ORIGINAL" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    # Audit shows the restore op
    audit2 = await store.recent_audit_for_skill("demo")
    assert audit2[0].op == "restore"
    assert audit2[0].actor == "user:restore"


async def test_restore_with_unknown_hash_lists_versions(wired, tmp_path: Path) -> None:
    cmd, root, store = wired
    src = tmp_path / "src" / "u"
    _write_skill_md(src, name="u")
    await cmd.handle(f"add {src}", _state())
    out = await cmd.handle("restore u --version no-such-hash", _state())
    assert "no audit entry matches" in out
    assert "Recent versions of 'u' you can restore" in out
    assert "--version " in out  # the prompt to copy-paste a valid hash


async def test_restore_missing_version_flag_lists_versions(wired, tmp_path: Path) -> None:
    cmd, root, _ = wired
    src = tmp_path / "src" / "v"
    _write_skill_md(src, name="v")
    await cmd.handle(f"add {src}", _state())
    out = await cmd.handle("restore v", _state())
    assert "missing --version flag" in out
    assert "--version " in out


async def test_restore_with_no_history_for_skill(wired) -> None:
    cmd, _, _ = wired
    out = await cmd.handle("restore ghost --version abc", _state())
    assert "no audit history" in out


async def test_restore_after_rm_resurrects_skill(wired, tmp_path: Path) -> None:
    """Most important UX: user accidentally `/skill rm`s, then restores."""
    cmd, root, store = wired
    src = tmp_path / "src" / "lost"
    _write_skill_md(src, name="lost", body="BODY OF LOST SKILL")
    await cmd.handle(f"add {src}", _state())
    skill_dir = root / "installed" / "lost"
    assert skill_dir.exists()
    # Delete
    await cmd.handle("rm lost YES", _state())
    assert not skill_dir.exists()
    # The delete audit entry holds the snapshot; restore via its before_hash
    audit = await store.recent_audit_for_skill("lost")
    delete_entry = next(e for e in audit if e.op == "delete")
    assert delete_entry.before_hash is not None
    target_hash = delete_entry.before_hash[:12]
    out = await cmd.handle(f"restore lost --version {target_hash}", _state())
    assert "✓ Restored" in out
    assert skill_dir.exists()
    assert "BODY OF LOST SKILL" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
