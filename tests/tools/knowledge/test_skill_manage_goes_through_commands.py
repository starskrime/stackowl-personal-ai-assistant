"""Story 4.9, AC1 — ``skill_manage``'s create/edit/patch/delete/enable/
disable each submit their declared ``skill.*`` command instead of calling
``record_skill_mutation``/``SkillIndexStore.delete``/``.set_enabled``
directly.

Mirrors ``test_skill_manage.py``'s own ``wired`` fixture shape (a REAL
``SkillIndexStore`` over a tmp SQLite ``DbPool`` and a REAL skills tree),
adding a ``submit_command`` spy to prove exactly one declared command type
is submitted per action.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import stackowl.tools.knowledge.skill_commands as skc
import stackowl.tools.knowledge.skill_manage as sm
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.knowledge.skill_manage import SkillManageTool

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

    from stackowl.db.pool import DbPool

_VALID_BODY = (
    "---\nname: {name}\ndescription: a test procedure\n---\n\n"
    "## Steps\n\n1. Do the thing carefully.\n2. Verify the result.\n"
)


def _skill_md(name: str, body: str = "") -> str:
    if body:
        return f"---\nname: {name}\ndescription: a test procedure\n---\n\n{body}\n"
    return _VALID_BODY.format(name=name)


@pytest.fixture()
def wired(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[SkillManageTool, SkillIndexStore]]:
    workspace = tmp_path / "workspace"
    (workspace / "skills" / "learned").mkdir(parents=True)
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(workspace))
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "skills" / "learned").mkdir(parents=True, exist_ok=True)

    store = SkillIndexStore(tmp_db)

    async def _fake_reindex(loader, store_, skills_root, *, embedding_registry=None):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr(skc, "reindex_after_change", _fake_reindex)

    services = StepServices(skill_store=store, db_pool=tmp_db)
    token = set_services(services)
    try:
        yield SkillManageTool(), store
    finally:
        reset_services(token)


async def _seed_index(store: SkillIndexStore, name: str) -> None:
    """Upsert the just-created on-disk skill into the index — mirrors
    ``test_skill_manage.py``'s own helper: the fake reindex here keeps the
    loader/embedder out, so edit/patch/delete/toggle need the row upserted
    directly from disk (production's real ``reindex_after_change`` does
    this)."""
    from stackowl.paths import StackowlHome
    from stackowl.skills.loader import LoadedSkill
    from stackowl.skills.manifest import SkillManifest
    from stackowl.skills.skill_md import parse_skill_md

    path = StackowlHome.skills_dir() / "learned" / name
    parsed = parse_skill_md((path / "SKILL.md").read_text("utf-8"))
    fm = dict(parsed.frontmatter)
    fm["source"] = "learned"
    manifest = SkillManifest.model_validate(fm)
    await store.upsert(
        LoadedSkill(manifest=manifest, path=path, body=parsed.body, tools_registered=0, owls_registered=0),
    )


def _spy_submit_command(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = sm.submit_command

    async def _spy(db, command_type, payload, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command_type)
        return await real(db, command_type, payload, **kwargs)

    monkeypatch.setattr(sm, "submit_command", _spy)
    return calls


class TestStructuralNoDirectMutatorCalls:
    @pytest.mark.parametrize(
        "method_name", ["_create", "_edit", "_patch", "_delete", "_set_enabled"],
    )
    def test_action_method_never_calls_record_skill_mutation_or_store_directly(
        self, method_name: str,
    ) -> None:
        src = inspect.getsource(getattr(SkillManageTool, method_name))
        assert "submit_command" in src, f"{method_name} never calls submit_command"
        for forbidden in ("record_skill_mutation(", "store.delete(", "store.set_enabled("):
            assert forbidden not in src, f"{method_name} still calls {forbidden!r} directly"


async def test_create_submits_skill_author_create(
    wired: tuple[SkillManageTool, SkillIndexStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, store = wired
    calls = _spy_submit_command(monkeypatch)
    res = await tool.execute(action="create", name="brew-coffee", content=_skill_md("brew-coffee"))
    assert res.success, res.error
    assert calls == [skc.AUTHOR_CREATE]
    from stackowl.paths import StackowlHome

    assert (StackowlHome.skills_dir() / "learned" / "brew-coffee" / "SKILL.md").exists()


async def test_edit_submits_skill_author_edit(
    wired: tuple[SkillManageTool, SkillIndexStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, store = wired
    await tool.execute(action="create", name="proc", content=_skill_md("proc"))
    await _seed_index(store, "proc")
    calls = _spy_submit_command(monkeypatch)
    res = await tool.execute(
        action="edit", name="proc", content=_skill_md("proc", body="## Steps\n\nUpdated.\n"),
    )
    assert res.success, res.error
    assert calls == [skc.AUTHOR_EDIT]


async def test_patch_submits_skill_author_patch(
    wired: tuple[SkillManageTool, SkillIndexStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, store = wired
    await tool.execute(
        action="create", name="patchme",
        content=_skill_md("patchme", body="## Steps\n\n1. OLD step.\n"),
    )
    await _seed_index(store, "patchme")
    calls = _spy_submit_command(monkeypatch)
    res = await tool.execute(action="patch", name="patchme", find="OLD step", replace="NEW step")
    assert res.success, res.error
    assert calls == [skc.AUTHOR_PATCH]


async def test_delete_submits_skill_delete_and_undo_restores(
    wired: tuple[SkillManageTool, SkillIndexStore], monkeypatch: pytest.MonkeyPatch,
    tmp_db: DbPool,
) -> None:
    from stackowl.commands.spec.undo import request_undo

    tool, store = wired
    await tool.execute(action="create", name="goner", content=_skill_md("goner"))
    await _seed_index(store, "goner")

    from stackowl.paths import StackowlHome

    goner_dir = StackowlHome.skills_dir() / "learned" / "goner"

    calls = _spy_submit_command(monkeypatch)
    res = await tool.execute(action="delete", name="goner")
    assert res.success, res.error
    assert calls == [skc.DELETE]
    assert await store.get("learned", "goner") is None
    assert not goner_dir.exists()

    rows = await tmp_db.fetch_all(
        "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
        (skc.DELETE,),
    )
    undo_outcome = await request_undo(tmp_db, rows[0]["command_id"])
    assert undo_outcome.refusal is None, undo_outcome.refusal
    assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
    assert undo_outcome.submission.outcome.success
    assert goner_dir.exists()  # resurrected on disk


async def test_enable_disable_submit_skill_set_enabled(
    wired: tuple[SkillManageTool, SkillIndexStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, store = wired
    await tool.execute(action="create", name="togglable", content=_skill_md("togglable"))
    await _seed_index(store, "togglable")

    calls = _spy_submit_command(monkeypatch)
    res_d = await tool.execute(action="disable", name="togglable")
    assert res_d.success, res_d.error
    res_e = await tool.execute(action="enable", name="togglable")
    assert res_e.success, res_e.error
    assert calls == [skc.SET_ENABLED, skc.SET_ENABLED]
