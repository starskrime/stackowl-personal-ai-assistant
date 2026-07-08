"""Tests for ``/skill`` slash command + install helpers (Commit 3 sub-phase 3b)."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from stackowl.commands.registry import CommandRegistry
from stackowl.commands.skill_command import SkillCommand
from stackowl.commands.skill_helpers import (
    SkillInstallError,
    hash_dir,
    install_from_archive_url,
    install_from_local_path,
    resolve_install_name,
)
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.registry import ToolRegistry

# Module-level asyncio mark would warn on sync tests; we mark async tests via
# the per-fixture/per-test mechanism (pytest-asyncio auto mode picks them up).


def _write_skill_md(dir_: Path, name: str, *, body: str = "Body.") -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _make_state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="", channel="cli",
        owl_name="system", pipeline_step="start",
    )


def _text(out: object) -> str:
    """Unwrap a CommandResponse to its text, or pass through a plain str."""
    return out.text if hasattr(out, "text") else out  # type: ignore[return-value]


@pytest.fixture()
async def wired_command(tmp_db: DbPool, tmp_path: Path):
    """SkillCommand wired against tmp workspace + SQLite — yields (cmd, root, store).

    Snapshots the singleton CommandRegistry so other test files that depend on
    pre-loaded builtins (e.g. /help discovery in test_story_3_1_4) don't see
    test pollution from this fixture.
    """
    skills_root = tmp_path / "workspace" / "skills"
    skills_root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db,
        tool_registry=ToolRegistry(),
        owl_registry=OwlRegistry(),
        skills_root=skills_root,
        builtin_seed_dir=tmp_path / "no_builtins",  # doesn't exist → no-op
    )
    registry = CommandRegistry.instance()
    snapshot = dict(registry._commands)  # type: ignore[attr-defined]
    cmd = SkillCommand.create_and_register(
        store=components.store, loader=components.loader, skills_root=skills_root,
    )
    try:
        yield cmd, skills_root, components.store
    finally:
        registry._commands = snapshot  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_hash_dir_changes_when_content_changes(tmp_path: Path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "a.txt").write_text("v1")
    h1 = hash_dir(d)
    (d / "a.txt").write_text("v2")
    h2 = hash_dir(d)
    assert h1 != h2


def test_hash_dir_stable_across_calls(tmp_path: Path) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "a.txt").write_text("v1")
    (d / "b.txt").write_text("v2")
    assert hash_dir(d) == hash_dir(d)


def test_resolve_install_name_no_conflict(tmp_path: Path) -> None:
    assert resolve_install_name(tmp_path, "foo") == "foo"


def test_resolve_install_name_appends_suffix(tmp_path: Path) -> None:
    (tmp_path / "foo").mkdir()
    assert resolve_install_name(tmp_path, "foo") == "foo-1"
    (tmp_path / "foo-1").mkdir()
    assert resolve_install_name(tmp_path, "foo") == "foo-2"


async def test_install_from_local_rejects_missing_skill_md(tmp_path: Path) -> None:
    src = tmp_path / "no-skill-md"
    src.mkdir()
    with pytest.raises(SkillInstallError, match="no SKILL.md"):
        await install_from_local_path(src, tmp_path / "ws")


async def test_install_uses_manifest_name_not_source_dir_name(tmp_path: Path) -> None:
    """Regression (smoke caught it): source dir name and manifest name can
    differ. The install target dir + index row must use the manifest name."""
    src = tmp_path / "src" / "weird-dir-name"  # not what the manifest says
    _write_skill_md(src, name="actual-skill-name")
    ws = tmp_path / "ws"
    result = await install_from_local_path(src, ws)
    assert result.name == "actual-skill-name"
    assert (ws / "installed" / "actual-skill-name" / "SKILL.md").exists()
    assert not (ws / "installed" / "weird-dir-name").exists()


async def test_install_from_local_copies_and_renames(tmp_path: Path) -> None:
    src = tmp_path / "src" / "mine"
    _write_skill_md(src, name="mine")
    ws = tmp_path / "ws"
    (ws / "installed" / "mine").mkdir(parents=True)  # pre-existing collision
    # Pre-existing dir needs a SKILL.md too so the loader is happy if anyone
    # scans (this test only checks rename behavior).
    (ws / "installed" / "mine" / "SKILL.md").write_text(
        "---\nname: mine\ndescription: stub\n---\n", encoding="utf-8",
    )
    result = await install_from_local_path(src, ws)
    assert result.name == "mine-1"
    assert (result.path / "SKILL.md").exists()


async def test_install_from_archive_zip(tmp_path: Path) -> None:
    """Archive install correctly extracts a zip with SKILL.md at the root."""
    # Build a tiny zip in memory containing a single SKILL.md under a top dir.
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("my-zip-skill/SKILL.md",
                    "---\nname: my-zip-skill\ndescription: x\n---\nbody\n")
    data = buf.getvalue()
    # Patch httpx.AsyncClient.get to return our bytes.
    from unittest.mock import AsyncMock, patch

    class FakeResp:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None: pass

    with patch("stackowl.commands.skill_helpers.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=FakeResp(data),
        )
        result = await install_from_archive_url(
            "https://example.com/x.zip", tmp_path / "ws",
        )
    assert result.name == "my-zip-skill"
    assert (result.path / "SKILL.md").exists()


async def test_install_from_archive_rejects_traversal(tmp_path: Path) -> None:
    """Path-traversal zip entries must be rejected."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "evil")
    data = buf.getvalue()
    from unittest.mock import AsyncMock, patch

    class FakeResp:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None: pass

    with patch("stackowl.commands.skill_helpers.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=FakeResp(data),
        )
        with pytest.raises(SkillInstallError, match="path-traversal"):
            await install_from_archive_url("https://x.example/y.zip", tmp_path / "ws")


async def test_install_from_archive_rejects_unrecognized_format(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, patch

    class FakeResp:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None: pass

    with patch("stackowl.commands.skill_helpers.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=FakeResp(b"not an archive at all"),
        )
        with pytest.raises(SkillInstallError, match="not a recognized archive"):
            await install_from_archive_url("https://x/y", tmp_path / "ws")


# ---------------------------------------------------------------------------
# /skill list / show / enable / disable / reload
# ---------------------------------------------------------------------------

async def test_list_empty_workspace(wired_command) -> None:
    cmd, _, _ = wired_command
    out = await cmd.handle("list", _make_state())
    assert "No skills installed" in _text(out)


async def test_list_shows_user_skills(wired_command) -> None:
    cmd, root, _ = wired_command
    _write_skill_md(root / "user" / "alpha", name="alpha")
    _write_skill_md(root / "user" / "beta", name="beta")
    await cmd.handle("reload", _make_state())
    out = _text(await cmd.handle("list", _make_state()))
    assert "alpha" in out and "beta" in out


async def test_list_filters_by_source(wired_command) -> None:
    cmd, root, _ = wired_command
    _write_skill_md(root / "user" / "u1", name="u1")
    _write_skill_md(root / "installed" / "i1", name="i1")
    await cmd.handle("reload", _make_state())
    out_user = _text(await cmd.handle("list --source user", _make_state()))
    assert "u1" in out_user and "i1" not in out_user
    out_installed = _text(await cmd.handle("list --source installed", _make_state()))
    assert "i1" in out_installed and "u1" not in out_installed


async def test_list_rejects_invalid_source(wired_command) -> None:
    cmd, _, _ = wired_command
    out = await cmd.handle("list --source bogus", _make_state())
    assert "invalid source" in out


async def test_show_renders_body(wired_command) -> None:
    cmd, root, _ = wired_command
    _write_skill_md(root / "user" / "demo", name="demo", body="THE BODY HERE")
    await cmd.handle("reload", _make_state())
    out = await cmd.handle("show demo", _make_state())
    assert "Skill: demo" in out
    assert "THE BODY HERE" in out


async def test_show_missing_skill(wired_command) -> None:
    cmd, _, _ = wired_command
    out = await cmd.handle("show nonexistent", _make_state())
    assert "no skill matching" in out


async def test_enable_disable_toggles_and_audits(wired_command) -> None:
    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "togglable", name="togglable")
    await cmd.handle("reload", _make_state())
    out_d = await cmd.handle("disable togglable", _make_state())
    assert "disabled" in out_d
    sk = await store.get("user", "togglable")
    assert sk is not None and sk.enabled is False
    out_e = await cmd.handle("enable togglable", _make_state())
    assert "enabled" in out_e
    audit = await store.recent_audit_for_skill("togglable")
    ops = [e.op for e in audit]
    assert "enable" in ops and "disable" in ops


async def test_edit_returns_path_for_user_skill(wired_command) -> None:
    cmd, root, _ = wired_command
    _write_skill_md(root / "user" / "editable", name="editable")
    await cmd.handle("reload", _make_state())
    out = await cmd.handle("edit editable", _make_state())
    assert "editable/SKILL.md" in out
    assert "/skill reload" in out


# ---------------------------------------------------------------------------
# /skill add (local path) + /skill rm + audit history via /skill diff
# ---------------------------------------------------------------------------

async def test_add_local_then_remove_round_trip(
    wired_command, tmp_path: Path,
) -> None:
    cmd, root, store = wired_command
    src = tmp_path / "src" / "my-new-skill"
    _write_skill_md(src, name="my-new-skill")
    out_add = await cmd.handle(f"add {src}", _make_state())
    assert "Installed" in out_add
    assert (root / "installed" / "my-new-skill" / "SKILL.md").exists()
    # Diff should now show the create entry.
    out_diff = await cmd.handle("diff my-new-skill", _make_state())
    assert "create" in out_diff
    assert "user:local" in out_diff
    # Remove (without confirmation → prompt).
    out_rm_prompt = await cmd.handle("rm my-new-skill", _make_state())
    assert "Confirm removal" in out_rm_prompt
    # Remove (with YES).
    out_rm = await cmd.handle("rm my-new-skill YES", _make_state())
    assert "Removed skill" in out_rm
    assert not (root / "installed" / "my-new-skill").exists()


async def test_add_renames_on_conflict(wired_command, tmp_path: Path) -> None:
    cmd, root, store = wired_command
    src = tmp_path / "src" / "duplicate"
    _write_skill_md(src, name="duplicate")
    # Install once.
    await cmd.handle(f"add {src}", _make_state())
    # Install again → must auto-rename to duplicate-1.
    out = await cmd.handle(f"add {src}", _make_state())
    assert "duplicate-1" in out
    assert (root / "installed" / "duplicate").exists()
    assert (root / "installed" / "duplicate-1").exists()
    # Regression guard (smoke surfaced this) — BOTH renamed installs must
    # show up in the SQLite index. The second install's SKILL.md must have
    # been rewritten to ``name: duplicate-1`` so the (source, name) UNIQUE
    # doesn't make the second install overwrite the first row.
    listing = _text(await cmd.handle("list --source installed", _make_state()))
    assert "duplicate" in listing
    assert "duplicate-1" in listing
    sk_a = await store.get("installed", "duplicate")
    sk_b = await store.get("installed", "duplicate-1")
    assert sk_a is not None and sk_a.path.endswith("/duplicate")
    assert sk_b is not None and sk_b.path.endswith("/duplicate-1")
    # The rewritten SKILL.md inside duplicate-1 must reflect the new name.
    md_b = (root / "installed" / "duplicate-1" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: duplicate-1" in md_b


async def test_rm_refuses_builtin(wired_command, tmp_path: Path) -> None:
    cmd, root, _ = wired_command
    _write_skill_md(root / "builtin" / "shipped", name="shipped")
    await cmd.handle("reload", _make_state())
    out = await cmd.handle("rm shipped YES", _make_state())
    assert "cannot remove built-in" in out


async def test_unknown_subcommand_returns_usage(wired_command) -> None:
    cmd, _, _ = wired_command
    out = await cmd.handle("totallyfake whatever", _make_state())
    assert "Usage:" in out


async def test_no_args_returns_usage(wired_command) -> None:
    cmd, _, _ = wired_command
    out = await cmd.handle("", _make_state())
    assert "Usage:" in out
