"""Tests for the E4-S4 ``skills_list`` tool.

``skills_list`` is a pure read: union ``list_for_source`` across every source on
a REAL :class:`SkillIndexStore` (temp SQLite ``DbPool``), apply category/disabled
filters + a (category, name) sort, and emit a terse one-line-per-skill listing
(never bodies). An empty index → empty list (success, not error).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest, SkillSource
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.knowledge.skills_list import SkillsListTool

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

    from stackowl.db.pool import DbPool


async def _seed(
    store: SkillIndexStore,
    *,
    name: str,
    source: SkillSource = "builtin",
    description: str = "a procedure",
    enabled: bool = True,
    tags: list[str] | None = None,
) -> None:
    """Upsert one index row. ``tags[0]`` becomes the skill's category (the
    manifest has no dedicated category field — categorization rides in tags)."""
    manifest = SkillManifest.model_validate({
        "name": name,
        "description": description,
        "source": source,
        "enabled": enabled,
        "tags": tags or [],
    })
    await store.upsert(
        LoadedSkill(
            manifest=manifest, path=Path("/nonexistent") / source / name,
            body="## Body\n\nirrelevant for listing.\n",
            tools_registered=0, owls_registered=0,
        )
    )


@pytest.fixture()
def wired(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[SkillsListTool, SkillIndexStore]]:
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(workspace))
    store = SkillIndexStore(tmp_db)
    token = set_services(StepServices(skill_store=store, db_pool=tmp_db))
    try:
        yield SkillsListTool(), store
    finally:
        reset_services(token)


# --------------------------------------------------------------------- happy


async def test_list_all_enabled(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="alpha", source="builtin", tags=["analysis"])
    await _seed(store, name="beta", source="user", tags=["writing"])
    res = await tool.execute()
    assert res.success, res.error
    assert "builtin:alpha" in res.output
    assert "user:beta" in res.output
    assert "2 skill(s)" in res.output
    # Terse: no body dumped.
    assert "irrelevant for listing" not in res.output


async def test_list_is_sorted_by_category_then_name(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="zed", source="builtin", tags=["analysis"])
    await _seed(store, name="abe", source="builtin", tags=["analysis"])
    await _seed(store, name="mid", source="builtin", tags=["writing"])
    res = await tool.execute()
    assert res.success, res.error
    # Within 'analysis', abe precedes zed; 'analysis' precedes 'writing'.
    out = res.output
    assert out.index("abe") < out.index("zed") < out.index("mid")


# ---------------------------------------------------------------------- filter


async def test_filter_by_category_subset(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="analyze-it", source="builtin", tags=["analysis"])
    await _seed(store, name="write-it", source="builtin", tags=["writing"])
    res = await tool.execute(category="analysis")
    assert res.success, res.error
    assert "analyze-it" in res.output
    assert "write-it" not in res.output
    assert "1 skill(s)" in res.output


async def test_unknown_category_empty(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="alpha", source="builtin", tags=["analysis"])
    res = await tool.execute(category="nope")
    assert res.success, res.error
    assert "(no skills)" in res.output


async def test_disabled_excluded_by_default(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="on", source="builtin", enabled=True)
    await _seed(store, name="off", source="builtin", enabled=False)
    res = await tool.execute()
    assert res.success, res.error
    assert "builtin:on" in res.output
    assert "builtin:off" not in res.output


async def test_disabled_included_when_requested(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="on", source="builtin", enabled=True)
    await _seed(store, name="off", source="builtin", enabled=False)
    res = await tool.execute(disabled=True)
    assert res.success, res.error
    assert "builtin:on" in res.output
    assert "builtin:off" in res.output
    assert "disabled" in res.output


async def test_platform_arg_is_noop_with_note(wired) -> None:  # noqa: ANN001
    tool, store = wired
    await _seed(store, name="alpha", source="builtin")
    res = await tool.execute(platform="linux")
    assert res.success, res.error
    assert "platform=linux" in res.output  # surfaced, not silently dropped
    assert "builtin:alpha" in res.output


# ----------------------------------------------------------------------- empty


async def test_empty_store_empty_list(wired) -> None:  # noqa: ANN001
    tool, _store = wired
    res = await tool.execute()
    assert res.success, res.error  # empty is success, not an error
    assert "(no skills)" in res.output


# ------------------------------------------------------------ store unavailable


async def test_store_unavailable_structured() -> None:
    token = set_services(StepServices(skill_store=None))
    try:
        res = await SkillsListTool().execute()
        assert not res.success
        assert "skills unavailable" in (res.error or "")
    finally:
        reset_services(token)


# --------------------------------------------------------------------- manifest


def test_manifest_severity_and_group() -> None:
    m = SkillsListTool().manifest
    assert m.name == "skills_list"
    assert m.action_severity == "read"
    assert m.toolset_group == "knowledge"


def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    tool = reg.get("skills_list")
    assert tool is not None
    assert tool.manifest.action_severity == "read"
