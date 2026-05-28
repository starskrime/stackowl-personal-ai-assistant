"""Tests for Learning Commit 3 sub-phase 3a — SkillLoader + SkillIndexStore.

Covers the SKILL.md parser, manifest validation, source-dir scanning, the
StackOwl-specific tools/ and owls.yaml sidecars (replacing the deleted
SkillPackLoader's coverage), the SQLite index store, and the assembly factory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.loader import SkillLoader
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.skill_md import SkillMarkdownError, parse_skill_md
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.registry import ToolRegistry

# asyncio mark is applied per-test below (via the asyncio_mode auto config
# defaults) — module-level mark would warn on the sync parser/manifest tests.


# ---------------------------------------------------------------------------
# parse_skill_md — frontmatter parser
# ---------------------------------------------------------------------------

def test_parse_skill_md_splits_frontmatter_and_body() -> None:
    text = (
        "---\n"
        "name: foo\n"
        "description: bar\n"
        "---\n"
        "\n"
        "# Body\n"
        "step 1\n"
    )
    parsed = parse_skill_md(text)
    assert parsed.frontmatter == {"name": "foo", "description": "bar"}
    assert parsed.body.startswith("# Body")
    assert "step 1" in parsed.body


def test_parse_skill_md_rejects_missing_frontmatter() -> None:
    with pytest.raises(SkillMarkdownError):
        parse_skill_md("# Just a heading, no frontmatter")


def test_parse_skill_md_rejects_unterminated_frontmatter() -> None:
    with pytest.raises(SkillMarkdownError):
        parse_skill_md("---\nname: foo\nno closing delimiter here\n")


def test_parse_skill_md_rejects_invalid_yaml() -> None:
    text = "---\nname: [unclosed\n---\nbody\n"
    with pytest.raises(SkillMarkdownError):
        parse_skill_md(text)


def test_parse_skill_md_rejects_non_mapping_frontmatter() -> None:
    text = "---\n- just\n- a\n- list\n---\nbody\n"
    with pytest.raises(SkillMarkdownError):
        parse_skill_md(text)


def test_parse_skill_md_handles_empty_body() -> None:
    text = "---\nname: foo\ndescription: bar\n---\n"
    parsed = parse_skill_md(text)
    assert parsed.body == ""


# ---------------------------------------------------------------------------
# SkillManifest — frontmatter validation
# ---------------------------------------------------------------------------

def test_skill_manifest_minimal_frontmatter() -> None:
    m = SkillManifest(name="my-skill", description="when X happens")
    assert m.name == "my-skill"
    assert m.source == "user"  # default
    assert m.version == "0.1.0"  # default
    assert m.enabled is True


def test_skill_manifest_rejects_invalid_name() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="Bad Name", description="x")


def test_skill_manifest_rejects_invalid_semver() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="ok", description="x", version="latest")


def test_skill_manifest_clamps_success_rate_out_of_range() -> None:
    with pytest.raises(ValidationError):
        SkillManifest(name="ok", description="x", success_rate=1.5)


# ---------------------------------------------------------------------------
# SkillLoader — scanning + sidecar loading
# ---------------------------------------------------------------------------

def _write_skill_md(
    dir_: Path, name: str, *, description: str = "test",
    version: str = "0.1.0", body: str = "Recipe body here.\n",
) -> None:
    """Helper: emit a SKILL.md into ``dir_``."""
    dir_.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n"
    (dir_ / "SKILL.md").write_text(fm + "\n" + body, encoding="utf-8")


async def test_loader_skips_dir_without_skill_md(tmp_path: Path) -> None:
    """A directory under <source>/ with no SKILL.md is skipped (with a warning), not crashed."""
    (tmp_path / "user" / "no-md").mkdir(parents=True)
    loader = SkillLoader()
    loaded = await loader.load_all(tmp_path)
    assert loaded == []


async def test_loader_loads_minimal_skill(tmp_path: Path) -> None:
    _write_skill_md(tmp_path / "user" / "alpha", name="alpha")
    loader = SkillLoader()
    loaded = await loader.load_all(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].manifest.name == "alpha"
    assert loaded[0].manifest.source == "user"
    assert "Recipe body" in loaded[0].body


async def test_loader_forces_source_from_directory(tmp_path: Path) -> None:
    """Even if frontmatter says source=builtin, loader overrides with the dir name."""
    (tmp_path / "installed" / "x").mkdir(parents=True)
    (tmp_path / "installed" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: y\nsource: builtin\n---\nbody\n",
        encoding="utf-8",
    )
    loader = SkillLoader()
    loaded = await loader.load_all(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].manifest.source == "installed"  # not "builtin" from frontmatter


async def test_loader_scans_all_four_source_dirs(tmp_path: Path) -> None:
    for src in ("builtin", "installed", "user", "learned"):
        _write_skill_md(tmp_path / src / f"skill-{src}", name=f"skill-{src}")
    loader = SkillLoader()
    loaded = await loader.load_all(tmp_path)
    assert len(loaded) == 4
    sources = sorted(s.manifest.source for s in loaded)
    assert sources == ["builtin", "installed", "learned", "user"]


async def test_loader_skips_underscore_dirs(tmp_path: Path) -> None:
    """Dirs starting with ``_`` are reserved (e.g. _deprecated, __pycache__)."""
    _write_skill_md(tmp_path / "user" / "real", name="real")
    _write_skill_md(tmp_path / "user" / "_archived", name="archived")
    loader = SkillLoader()
    loaded = await loader.load_all(tmp_path)
    assert [s.manifest.name for s in loaded] == ["real"]


async def test_loader_registers_tool_subclass_from_sidecar(tmp_path: Path) -> None:
    """A user skill with tools/*.py auto-registers Tool subclasses."""
    skill_dir = tmp_path / "user" / "tool-skill"
    _write_skill_md(skill_dir, name="tool-skill")
    tools_dir = skill_dir / "tools"
    tools_dir.mkdir()
    (tools_dir / "demo.py").write_text(
        '''from __future__ import annotations
from stackowl.tools.base import Tool, ToolResult

class DemoTool(Tool):
    @property
    def name(self) -> str:
        return "demo-skill-tool"

    @property
    def description(self) -> str:
        return "demo"

    @property
    def parameters(self) -> dict:
        return {}

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=0.0)
''',
        encoding="utf-8",
    )
    tool_reg = ToolRegistry()
    loader = SkillLoader(tool_registry=tool_reg)
    loaded = await loader.load_all(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].tools_registered == 1
    assert tool_reg.get("demo-skill-tool") is not None


async def test_loader_resolves_nested_owl_manifest_path(tmp_path: Path) -> None:
    """Loader accepts both owls.yaml and owls/manifest.yaml for owl extension."""
    skill_dir = tmp_path / "user" / "owl-skill"
    _write_skill_md(skill_dir, name="owl-skill")
    owls_subdir = skill_dir / "owls"
    owls_subdir.mkdir()
    (owls_subdir / "manifest.yaml").write_text(
        yaml.dump([
            {
                "name": "skillowl",
                "role": "Testing owl from a skill pack",
                "system_prompt": "You are a test owl.",
                "model_tier": "fast",
            }
        ]),
        encoding="utf-8",
    )
    owl_reg = OwlRegistry()
    loader = SkillLoader(owl_registry=owl_reg)
    loaded = await loader.load_all(tmp_path)
    assert loaded[0].owls_registered == 1


async def test_loader_seeds_builtins_idempotently(tmp_path: Path) -> None:
    """``builtin_seed_dir`` contents land in ``<root>/builtin/`` on every boot."""
    seed_dir = tmp_path / "pkg_builtins"
    _write_skill_md(
        seed_dir / "ships-with-stackowl", name="ships-with-stackowl",
    )
    root = tmp_path / "workspace_skills"
    loader = SkillLoader()
    loaded = await loader.load_all(root, builtin_seed_dir=seed_dir)
    assert len(loaded) == 1
    assert loaded[0].manifest.source == "builtin"
    # Run twice — must remain idempotent (no duplicate row, no crash).
    loaded2 = await loader.load_all(root, builtin_seed_dir=seed_dir)
    assert len(loaded2) == 1


# ---------------------------------------------------------------------------
# SkillIndexStore — SQLite cache + audit
# ---------------------------------------------------------------------------

async def test_index_store_upserts_then_lists(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    _write_skill_md(tmp_path / "user" / "alpha", name="alpha")
    _write_skill_md(tmp_path / "user" / "beta", name="beta")
    store = SkillIndexStore(tmp_db)
    loader = SkillLoader()
    loaded = await loader.load_all(tmp_path, store=store)
    assert len(loaded) == 2
    user_skills = await store.list_for_source("user")
    assert sorted(s.name for s in user_skills) == ["alpha", "beta"]


async def test_index_store_upsert_preserves_runtime_stats(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """Re-scanning must not wipe success_rate / n_executions / embedding."""
    _write_skill_md(tmp_path / "user" / "stats-keeper", name="stats-keeper")
    store = SkillIndexStore(tmp_db)
    loader = SkillLoader()
    await loader.load_all(tmp_path, store=store)
    row = await store.get("user", "stats-keeper")
    assert row is not None
    await store.set_success_rate(row.skill_id, 0.87)
    await store.increment_n_executions(row.skill_id)
    await store.increment_n_executions(row.skill_id)
    # Re-scan disk.
    await loader.load_all(tmp_path, store=store)
    row_after = await store.get("user", "stats-keeper")
    assert row_after is not None
    assert row_after.success_rate == pytest.approx(0.87)
    assert row_after.n_executions == 2


async def test_index_store_set_enabled_toggles(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    _write_skill_md(tmp_path / "user" / "toggle-me", name="toggle-me")
    store = SkillIndexStore(tmp_db)
    loader = SkillLoader()
    await loader.load_all(tmp_path, store=store)
    sk = await store.get("user", "toggle-me")
    assert sk is not None and sk.enabled is True
    await store.set_enabled(sk.skill_id, enabled=False)
    sk2 = await store.get("user", "toggle-me")
    assert sk2 is not None and sk2.enabled is False
    enabled_list = await store.list_enabled()
    assert all(s.name != "toggle-me" for s in enabled_list)


async def test_index_store_audit_round_trip(tmp_db: DbPool) -> None:
    store = SkillIndexStore(tmp_db)
    await store.audit_write(
        skill_name="some-skill", source="learned", op="create",
        actor="agent", after_hash="abc123",
        details={"reason": "synthesized from 4 reflections"},
    )
    await store.audit_write(
        skill_name="some-skill", source="learned", op="update",
        actor="agent", before_hash="abc123", after_hash="def456",
    )
    entries = await store.recent_audit_for_skill("some-skill")
    assert len(entries) == 2
    # Newest-first.
    assert entries[0].op == "update"
    assert entries[1].op == "create"
    assert entries[1].details["reason"] == "synthesized from 4 reflections"


# ---------------------------------------------------------------------------
# SkillsAssembly — factory
# ---------------------------------------------------------------------------

async def test_assembly_build_wires_loader_store_and_loads_skills(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """SkillsAssembly.build should populate store with builtin + user skills."""
    skills_root = tmp_path / "workspace" / "skills"
    seed_dir = tmp_path / "pkg_builtin"
    _write_skill_md(seed_dir / "shipped", name="shipped")
    _write_skill_md(skills_root / "user" / "mine", name="mine")
    tool_reg = ToolRegistry()
    owl_reg = OwlRegistry()
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=tool_reg, owl_registry=owl_reg,
        skills_root=skills_root, builtin_seed_dir=seed_dir,
    )
    assert len(components.loaded) == 2
    names = sorted(s.manifest.name for s in components.loaded)
    assert names == ["mine", "shipped"]
    sources = sorted(s.manifest.source for s in components.loaded)
    assert sources == ["builtin", "user"]
