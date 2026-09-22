"""Story 4.9, AC1 — ``/skill``'s 8 mutating sub-commands (add/rm/enable/
disable/pin/unpin/reload/restore, plus dedupe/migrate) each submit their
declared ``skill.*`` command instead of calling ``install_from_*``/
``record_skill_mutation``/``SkillIndexStore.set_enabled``/``.set_pinned``
directly.

Mirrors ``tests/skills/test_skill_command.py``'s own ``wired_command``
fixture shape, adding a ``submit_command`` spy.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import stackowl.commands.skill_command as skcmd_mod
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.skill_command import SkillCommand
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.knowledge import skill_commands as skc
from stackowl.tools.registry import ToolRegistry


def _write_skill_md(dir_: Path, name: str, *, body: str = "Body.") -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n", encoding="utf-8",
    )


def _make_state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_key="s", input_text="", channel="cli",
        owl_name="system", pipeline_step="start",
    )


def _text(out: object) -> str:
    return out.text if hasattr(out, "text") else out  # type: ignore[return-value]


@pytest.fixture()
async def wired_command(tmp_db: DbPool, tmp_path: Path):
    skills_root = tmp_path / "workspace" / "skills"
    skills_root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
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


def _spy_submit_command(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = skcmd_mod.submit_command

    async def _spy(db, command_type, payload, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command_type)
        return await real(db, command_type, payload, **kwargs)

    monkeypatch.setattr(skcmd_mod, "submit_command", _spy)
    return calls


#: Real CALLS this shape's own method used to make directly, keyed per
#: method (never a blanket list — a method's own doc comment MENTIONING a
#: sibling helper by name, e.g. _set_enabled's docstring citing
#: record_skill_mutation, is not the same as CALLING it).
_FORBIDDEN_BY_METHOD: dict[str, tuple[str, ...]] = {
    "_add": ("install_from_local_path(", "install_from_git_url(", "install_from_archive_url(",
             "record_skill_mutation("),
    "_rm": ("record_skill_mutation(",),
    "_set_enabled": ("self._store.set_enabled(",),
    "_set_pinned": ("self._store.set_pinned(",),
    "_reload": ("reindex_after_change(",),
    "_restore": ("restore_snapshot(", "record_skill_mutation("),
    "_dedupe": ("SkillConsolidator(",),
    "_migrate": ("SkillStandardMigrator(",),
}


class TestStructuralNoDirectMutatorCalls:
    @pytest.mark.parametrize("method_name", sorted(_FORBIDDEN_BY_METHOD))
    def test_method_never_calls_a_mutator_directly(self, method_name: str) -> None:
        src = inspect.getsource(getattr(SkillCommand, method_name))
        assert "submit_command" in src, f"{method_name} never calls submit_command"
        for forbidden in _FORBIDDEN_BY_METHOD[method_name]:
            # A call, not a mention: the forbidden string must appear as a
            # real invocation on a non-comment line.
            hits = [
                line for line in src.splitlines()
                if forbidden in line and not line.strip().startswith("#")
            ]
            assert not hits, f"{method_name} still calls {forbidden!r} directly: {hits}"


async def test_add_submits_skill_install(
    wired_command, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,  # noqa: ANN001
) -> None:
    cmd, root, store = wired_command
    src = tmp_path / "src" / "my-new-skill"
    _write_skill_md(src, name="my-new-skill")

    calls = _spy_submit_command(monkeypatch)
    out = _text(await cmd.handle(f"add {src}", _make_state()))
    assert "Installed" in out
    assert calls == [skc.INSTALL]


async def test_rm_submits_skill_delete_and_undo_resurrects(
    wired_command, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_db: DbPool,  # noqa: ANN001
) -> None:
    from stackowl.commands.spec.undo import request_undo

    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "removable", name="removable")
    await cmd.handle("reload", _make_state())

    calls = _spy_submit_command(monkeypatch)
    out = _text(await cmd.handle("rm removable YES", _make_state()))
    assert "Removed" in out
    assert calls == [skc.DELETE]
    assert not (root / "user" / "removable").exists()

    rows = await tmp_db.fetch_all(
        "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
        (skc.DELETE,),
    )
    with cmd._bound_services():  # noqa: SLF001 — same ambient-services need as the forward call
        undo_outcome = await request_undo(tmp_db, rows[0]["command_id"])
    assert undo_outcome.refusal is None, undo_outcome.refusal
    assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
    assert undo_outcome.submission.outcome.success
    assert (root / "user" / "removable").exists()  # resurrected


async def test_enable_disable_submit_skill_set_enabled(
    wired_command, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "togglable", name="togglable")
    await cmd.handle("reload", _make_state())

    calls = _spy_submit_command(monkeypatch)
    out_d = _text(await cmd.handle("disable togglable", _make_state()))
    assert "disabled" in out_d
    out_e = _text(await cmd.handle("enable togglable", _make_state()))
    assert "enabled" in out_e
    assert calls == [skc.SET_ENABLED, skc.SET_ENABLED]


async def test_pin_unpin_submit_skill_set_pinned(
    wired_command, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "pinnable", name="pinnable")
    await cmd.handle("reload", _make_state())

    calls = _spy_submit_command(monkeypatch)
    out_p = _text(await cmd.handle("pin pinnable", _make_state()))
    assert "pinned" in out_p
    out_u = _text(await cmd.handle("unpin pinnable", _make_state()))
    assert "unpinned" in out_u
    assert calls == [skc.SET_PINNED, skc.SET_PINNED]


async def test_undo_a_set_enabled_flips_it_back(
    wired_command, tmp_db: DbPool,  # noqa: ANN001
) -> None:
    """Review finding, 2026-09-22 pass: no test exercised skill.set_enabled's
    declared self-undo via request_undo end-to-end."""
    from stackowl.commands.spec.undo import request_undo

    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "toggleme", name="toggleme")
    await cmd.handle("reload", _make_state())

    await cmd.handle("disable toggleme", _make_state())
    sk = await store.get("user", "toggleme")
    assert sk is not None and sk.enabled is False

    rows = await tmp_db.fetch_all(
        "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
        (skc.SET_ENABLED,),
    )
    with cmd._bound_services():  # noqa: SLF001
        undo_outcome = await request_undo(tmp_db, rows[0]["command_id"])
    assert undo_outcome.refusal is None, undo_outcome.refusal
    assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
    assert undo_outcome.submission.outcome.success

    sk = await store.get("user", "toggleme")
    assert sk is not None and sk.enabled is True


async def test_undo_a_set_pinned_flips_it_back(
    wired_command, tmp_db: DbPool,  # noqa: ANN001
) -> None:
    """Review finding, 2026-09-22 pass: no test exercised skill.set_pinned's
    declared self-undo via request_undo end-to-end."""
    from stackowl.commands.spec.undo import request_undo

    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "pinme", name="pinme")
    await cmd.handle("reload", _make_state())

    async def _pinned() -> bool:
        sk = await store.get("user", "pinme")
        assert sk is not None
        row = await tmp_db.fetch_all(
            "SELECT pinned FROM skills WHERE skill_id = ?", (sk.skill_id,),
        )
        return bool(row[0]["pinned"])

    await cmd.handle("pin pinme", _make_state())
    assert await _pinned() is True

    rows = await tmp_db.fetch_all(
        "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
        (skc.SET_PINNED,),
    )
    with cmd._bound_services():  # noqa: SLF001
        undo_outcome = await request_undo(tmp_db, rows[0]["command_id"])
    assert undo_outcome.refusal is None, undo_outcome.refusal
    assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
    assert undo_outcome.submission.outcome.success

    assert await _pinned() is False


async def test_a_different_skill_command_type_never_falsely_supersedes(
    wired_command, tmp_db: DbPool,  # noqa: ANN001
) -> None:
    """Review finding, 2026-09-22 pass: has_later_completed_command's
    target-key match had no command_type filter, so a LATER command of a
    DIFFERENT type sharing the same skill name (here: pin, after disable)
    falsely marked the earlier undo as "superseded"."""
    from stackowl.commands.spec.undo import request_undo

    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "collide", name="collide")
    await cmd.handle("reload", _make_state())

    await cmd.handle("disable collide", _make_state())
    disable_rows = await tmp_db.fetch_all(
        "SELECT command_id FROM tasks WHERE command_type = ? ORDER BY rowid DESC LIMIT 1",
        (skc.SET_ENABLED,),
    )
    # A DIFFERENT command type on the SAME skill name, completed AFTER the
    # disable — must never be read as superseding the disable's own undo.
    await cmd.handle("pin collide", _make_state())

    with cmd._bound_services():  # noqa: SLF001
        undo_outcome = await request_undo(tmp_db, disable_rows[0]["command_id"])
    assert undo_outcome.refusal is None, undo_outcome.refusal
    assert undo_outcome.submission is not None and undo_outcome.submission.outcome is not None
    assert undo_outcome.submission.outcome.success

    sk = await store.get("user", "collide")
    assert sk is not None and sk.enabled is True  # disable was undone


async def test_reload_submits_skill_reload_index(
    wired_command, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "alpha", name="alpha")

    calls = _spy_submit_command(monkeypatch)
    out = _text(await cmd.handle("reload", _make_state()))
    assert "Reloaded" in out
    assert calls == [skc.RELOAD_INDEX]


async def test_dedupe_submits_skill_dedupe_and_parks(
    wired_command, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """Review finding (verification-gap, 2026-09-22 pass): dedupe_spec is
    severity="write", reversible=False, so decide() always parks it for
    step-up — a real contract change from the pre-4.9 synchronous --apply
    behavior, previously untested at the command level."""
    cmd, root, store = wired_command
    _write_skill_md(root / "user" / "dedupe-a", name="dedupe-a")
    await cmd.handle("reload", _make_state())

    calls = _spy_submit_command(monkeypatch)
    out = _text(await cmd.handle("dedupe --apply", _make_state()))
    assert "pending your approval" in out
    assert calls == [skc.DEDUPE]
    # Nothing actually consolidated — the skill is still there, untouched.
    assert await store.get("user", "dedupe-a") is not None


async def test_migrate_submits_skill_migrate_and_parks(
    wired_command, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """Same review finding as dedupe's own park test, for migrate_spec."""
    base_cmd, root, store = wired_command
    _write_skill_md(root / "user" / "migrate-a", name="migrate-a")
    cmd = SkillCommand(
        store=store, loader=base_cmd._loader, skills_root=root,  # noqa: SLF001
        provider_registry=object(),
    )
    await cmd.handle("reload", _make_state())

    calls = _spy_submit_command(monkeypatch)
    out = _text(await cmd.handle("migrate --apply", _make_state()))
    assert "pending your approval" in out
    assert calls == [skc.MIGRATE_STANDARD]


async def test_restore_submits_skill_restore_version(
    wired_command, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,  # noqa: ANN001
) -> None:
    cmd, root, store = wired_command
    src = tmp_path / "src" / "demo"
    _write_skill_md(src, name="demo", body="ORIGINAL")
    await cmd.handle(f"add {src}", _make_state())
    skill_dir = root / "installed" / "demo"
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: x\n---\n\nCHANGED CONTENT\n", encoding="utf-8",
    )
    audit = await store.recent_audit_for_skill("demo")
    create_entry = next(e for e in audit if e.op == "create")
    target_hash = create_entry.after_hash[:12]

    calls = _spy_submit_command(monkeypatch)
    out = _text(await cmd.handle(f"restore demo --version {target_hash}", _make_state()))
    assert "✓ Restored" in out
    assert calls == [skc.RESTORE_VERSION]
    assert "ORIGINAL" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
