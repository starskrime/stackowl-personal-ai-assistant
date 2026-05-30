"""Tests for the E4-S3 ``skill_view`` tool.

``skill_view`` is a pure read: resolve a skill by qualified ('source:name') or
bare name via a REAL :class:`SkillIndexStore` (temp SQLite ``DbPool`` from the
``tmp_db`` fixture), return its body + any ``references/*.md`` subloaded one
level deep from a REAL skills tree under a tmp ``STACKOWL_DATA_DIR``.

The store row's ``path`` points at the on-disk skill dir, so the fixture writes
both the index row AND the on-disk dir (incl. an optional references file) so
the subload is genuine.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest, SkillSource
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.knowledge.skill_view import SkillViewTool

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

    from stackowl.db.pool import DbPool


async def _seed(
    store: SkillIndexStore,
    workspace: Path,
    *,
    name: str,
    source: SkillSource = "builtin",
    description: str = "a test procedure",
    body: str = "## Steps\n\n1. Do the thing.\n",
    enabled: bool = True,
    tags: list[str] | None = None,
    references: dict[str, str] | None = None,
) -> None:
    """Write an on-disk skill dir (SKILL.md + optional references/*.md) AND its
    index row, so skill_view can resolve via the store and subload from disk."""
    skill_dir = workspace / "skills" / source / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_tags = "\n".join(f"  - {t}" for t in (tags or []))
    tags_block = f"tags:\n{fm_tags}\n" if tags else ""
    skill_md = (
        f"---\nname: {name}\ndescription: {description}\n"
        f"enabled: {str(enabled).lower()}\n{tags_block}---\n\n{body}"
    )
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    if references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for fname, fbody in references.items():
            (refs_dir / fname).write_text(fbody, encoding="utf-8")

    manifest = SkillManifest.model_validate({
        "name": name,
        "description": description,
        "source": source,
        "enabled": enabled,
        "tags": tags or [],
    })
    await store.upsert(
        LoadedSkill(
            manifest=manifest, path=skill_dir, body=body,
            tools_registered=0, owls_registered=0,
        )
    )


@pytest.fixture()
def wired(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[SkillViewTool, SkillIndexStore, Path]]:
    """skill_view wired to a real store + tmp skills tree under STACKOWL_DATA_DIR."""
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(workspace))
    store = SkillIndexStore(tmp_db)
    token = set_services(StepServices(skill_store=store, db_pool=tmp_db))
    try:
        yield SkillViewTool(), store, workspace
    finally:
        reset_services(token)


# --------------------------------------------------------------------- happy


async def test_view_qualified_with_references(wired) -> None:  # noqa: ANN001
    tool, store, workspace = wired
    await _seed(
        store, workspace, name="brainstorming", source="builtin",
        body="## Procedure\n\nThink widely, then narrow.\n",
        references={"extra.md": "# Extra\n\nAdditional context for the procedure.\n"},
    )
    res = await tool.execute(name="builtin:brainstorming")
    assert res.success, res.error
    assert "builtin:brainstorming" in res.output
    assert "Think widely, then narrow." in res.output
    # Linked reference subloaded one level.
    assert "references/extra.md" in res.output
    assert "Additional context for the procedure." in res.output
    assert "Linked references (1)" in res.output


async def test_view_bare_name_resolves_across_sources(wired) -> None:  # noqa: ANN001
    tool, store, workspace = wired
    await _seed(store, workspace, name="lonely", source="user", body="## Body\n\nsolo.\n")
    res = await tool.execute(name="lonely")
    assert res.success, res.error
    assert "user:lonely" in res.output
    assert "solo." in res.output


# ---------------------------------------------------------------------- edge


async def test_view_no_references_body_only(wired) -> None:  # noqa: ANN001
    tool, store, workspace = wired
    await _seed(store, workspace, name="plain", source="builtin", body="## Body\n\nno refs here.\n")
    res = await tool.execute(name="builtin:plain")
    assert res.success, res.error
    assert "no refs here." in res.output
    assert "Linked references" not in res.output


async def test_unknown_qualified_name_structured_error(wired) -> None:  # noqa: ANN001
    tool, _store, _ws = wired
    res = await tool.execute(name="builtin:does-not-exist")
    assert not res.success
    assert "not found" in (res.error or "")
    assert res.output == ""


async def test_unknown_bare_name_structured_error(wired) -> None:  # noqa: ANN001
    tool, _store, _ws = wired
    res = await tool.execute(name="ghost")
    assert not res.success
    assert "not found" in (res.error or "")


async def test_empty_name_structured_error(wired) -> None:  # noqa: ANN001
    tool, _store, _ws = wired
    res = await tool.execute(name="   ")
    assert not res.success
    assert "non-empty" in (res.error or "")


async def test_unknown_source_qualifier_falls_back_to_bare(wired) -> None:  # noqa: ANN001
    # 'weird:foo' has an unrecognized source → falls back to a whole-string bare
    # lookup, which also misses → structured not-found (never a raise).
    tool, _store, _ws = wired
    res = await tool.execute(name="weird:foo")
    assert not res.success
    assert "not found" in (res.error or "")


async def test_disabled_skill_still_viewable_with_marker(wired) -> None:  # noqa: ANN001
    tool, store, workspace = wired
    await _seed(store, workspace, name="off", source="learned", enabled=False, body="## Body\n\nx.\n")
    res = await tool.execute(name="learned:off")
    assert res.success, res.error
    assert "(disabled)" in res.output


# ------------------------------------------------------------ store unavailable


async def test_store_unavailable_structured() -> None:
    token = set_services(StepServices(skill_store=None))
    try:
        res = await SkillViewTool().execute(name="builtin:anything")
        assert not res.success
        assert "skills unavailable" in (res.error or "")
    finally:
        reset_services(token)


# --------------------------------------------------------------------- manifest


def test_manifest_severity_and_group() -> None:
    m = SkillViewTool().manifest
    assert m.name == "skill_view"
    assert m.action_severity == "read"
    assert m.toolset_group == "knowledge"


def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    tool = reg.get("skill_view")
    assert tool is not None
    assert tool.manifest.action_severity == "read"


async def test_reference_symlink_escape_not_read(wired, tmp_path) -> None:  # noqa: ANN001
    # Defense-in-depth: a references/ symlink pointing OUTSIDE the skill dir must
    # NOT be read (even though no shipped tool can plant one — local-FS trust).
    tool, store, workspace = wired
    await _seed(store, workspace, name="linker", source="learned", body="## Body\n\nok.\n")
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("TOPSECRET-EXFIL", encoding="utf-8")
    refs_dir = workspace / "skills" / "learned" / "linker" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    try:
        (refs_dir / "leak.md").symlink_to(secret)
    except OSError:
        import pytest
        pytest.skip("symlinks unsupported")
    res = await tool.execute(name="learned:linker")
    assert res.success, res.error
    assert "TOPSECRET-EXFIL" not in res.output  # symlink escape refused
