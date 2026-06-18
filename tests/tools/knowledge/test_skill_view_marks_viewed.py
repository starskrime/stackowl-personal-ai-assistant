"""Test: skill_view records mark_viewed in FOCUS_TRACKER after a successful view.

Task 8 — skill-tiering B: when skill_view resolves and returns a skill's playbook,
it must record the view in the hysteresis tracker so the skill stays stickier
next turn. This is a best-effort side-effect; the view itself must still succeed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.skill_focus import FOCUS_TRACKER
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.knowledge.skill_view import SkillViewTool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from stackowl.db.pool import DbPool


async def _seed_skill(
    store: SkillIndexStore,
    workspace: Path,
    *,
    name: str,
    source: str = "builtin",
    body: str = "## Steps\n\n1. Do the thing.\n",
) -> None:
    """Write a minimal on-disk skill dir + index row for skill_view to resolve."""
    skill_dir = workspace / "skills" / source / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = (
        f"---\nname: {name}\ndescription: a test skill\n"
        f"enabled: true\n---\n\n{body}"
    )
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    manifest = SkillManifest.model_validate({
        "name": name,
        "description": "a test skill",
        "source": source,
        "enabled": True,
        "tags": [],
    })
    await store.upsert(
        LoadedSkill(
            manifest=manifest, path=skill_dir, body=body,
            tools_registered=0, owls_registered=0,
        )
    )


@pytest.fixture()
async def wired_store(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[SkillIndexStore]:
    """Yield a SkillIndexStore wired into set_services, reset on exit."""
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(workspace))
    store = SkillIndexStore(tmp_db)
    token = set_services(StepServices(skill_store=store, db_pool=tmp_db))
    try:
        yield store
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_skill_view_marks_viewed(
    wired_store: SkillIndexStore, tmp_path: Path,
) -> None:
    """skill_view must call FOCUS_TRACKER.mark_viewed for the resolved skill."""
    FOCUS_TRACKER.clear_all()

    workspace = tmp_path / "workspace"
    await _seed_skill(wired_store, workspace, name="alpha")

    token = TraceContext.start(
        session_id="s1",
        trace_id="t1",
        interactive=True,
        channel="cli",
        owl_name="owl-test",
    )
    try:
        result = await SkillViewTool().execute(name="alpha")
        assert result.success, f"skill_view failed unexpectedly: {result.error}"
    finally:
        TraceContext.reset(token)

    # Now check the tracker: advance to the next turn and confirm bonus > 0.
    turn = FOCUS_TRACKER.begin_turn("owl-test", "s1")
    bonus = FOCUS_TRACKER.bonus_for("owl-test", "s1", "alpha", turn)
    assert bonus > 0.0, (
        f"Expected a view bonus for skill 'alpha' but got {bonus}. "
        "skill_view likely did not call FOCUS_TRACKER.mark_viewed."
    )


@pytest.mark.asyncio
async def test_skill_view_no_mark_when_skill_not_found(
    wired_store: SkillIndexStore,
) -> None:
    """skill_view must NOT mark_viewed when the skill is not resolved."""
    FOCUS_TRACKER.clear_all()

    token = TraceContext.start(
        session_id="s2",
        trace_id="t2",
        interactive=True,
        channel="cli",
        owl_name="owl-test",
    )
    try:
        result = await SkillViewTool().execute(name="nonexistent-skill")
        assert not result.success  # not found → structured error, no view
    finally:
        TraceContext.reset(token)

    turn = FOCUS_TRACKER.begin_turn("owl-test", "s2")
    bonus = FOCUS_TRACKER.bonus_for("owl-test", "s2", "nonexistent-skill", turn)
    assert bonus == 0.0, (
        f"No view should be recorded for a missing skill, but got bonus {bonus}."
    )
