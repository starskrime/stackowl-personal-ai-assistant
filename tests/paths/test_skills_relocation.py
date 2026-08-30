"""D05.1 — skills relocation: the security property, and the bug that ate 419 skills.

The migration test below is a REGRESSION test in the literal sense: the first
version of migrate_legacy_skills() destroyed 419 real skills on this machine, and
this reproduces the exact conditions that did it.
"""

from __future__ import annotations

import pytest

from stackowl.paths import (
    StackowlHome,
    migrate_legacy_skills,
    skills_dir_is_outside_workspace,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# The security property — the reason this item exists.
# --------------------------------------------------------------------------- #


def test_skills_live_outside_the_model_writable_workspace(home):
    """write_file confines to workspace(); SkillLoader exec_module()s
    skills/**/tools/*.py at boot. If those trees overlap, the model can write
    code that runs at the next start."""
    skills = StackowlHome.skills_dir().resolve()
    workspace = StackowlHome.workspace().resolve()
    assert workspace not in skills.parents
    assert skills != workspace
    assert skills_dir_is_outside_workspace()


def test_the_guard_catches_a_pathological_data_dir(home, monkeypatch):
    """STACKOWL_DATA_DIR moves workspace() but not home(), so the two are
    independent and the property is NOT implied by reading skills_dir() alone.
    Pointing the data dir at home makes workspace the PARENT of skills again."""
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(home))
    assert not skills_dir_is_outside_workspace(), (
        "the guard must report unsafe when workspace contains the skills tree"
    )


def test_write_file_can_no_longer_reach_the_skills_tree(home):
    """The concrete exploit, asserted directly: this path used to resolve INSIDE
    the skills tree, and the loader would exec it at the next boot."""
    from stackowl.tools.io.path_guard import resolve_in_workspace

    planted = resolve_in_workspace("skills/evil/tools/pwn.py").resolve()
    assert not str(planted).startswith(str(StackowlHome.skills_dir().resolve()))


# --------------------------------------------------------------------------- #
# The migration, and the bug it shipped with.
# --------------------------------------------------------------------------- #


def _legacy_skill(home, source: str, name: str) -> None:
    d = StackowlHome.workspace() / "skills" / source / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n\nbody\n", encoding="utf-8")


def test_migration_moves_skills_to_the_new_root(home):
    _legacy_skill(home, "learned", "alpha")
    _legacy_skill(home, "user", "beta")

    migrate_legacy_skills()

    assert (StackowlHome.skills_dir() / "learned" / "alpha" / "SKILL.md").exists()
    assert (StackowlHome.skills_dir() / "user" / "beta" / "SKILL.md").exists()
    assert not (StackowlHome.workspace() / "skills").exists()


def test_migration_survives_ensure_exists_having_precreated_the_subdirs(home):
    """THE REGRESSION. This is exactly what destroyed 419 skills.

    ensure_exists() mkdirs skills/{builtin,installed,user,learned} BEFORE the
    migration runs. The first version skipped any entry whose destination
    already existed — so all four freshly-created EMPTY dirs were treated as
    "already migrated" — and then verified only that each NAME existed at the
    target. Four empty dirs satisfied that, verify passed, and rmtree deleted
    the source.
    """
    _legacy_skill(home, "learned", "alpha")
    _legacy_skill(home, "learned", "gamma")
    _legacy_skill(home, "builtin", "delta")

    # Precisely the precondition ensure_exists() creates.
    for sub in ("builtin", "installed", "user", "learned"):
        (StackowlHome.skills_dir() / sub).mkdir(parents=True, exist_ok=True)

    migrate_legacy_skills()

    for source, name in (("learned", "alpha"), ("learned", "gamma"), ("builtin", "delta")):
        assert (StackowlHome.skills_dir() / source / name / "SKILL.md").exists(), (
            f"{source}/{name} was LOST — the empty-dir skip is back"
        )


def test_migration_keeps_the_source_when_verify_fails(home, monkeypatch):
    """Nothing is deleted unless the target holds at least as many FILES as the
    source. A name check is not enough — that is what passed on empty dirs."""
    _legacy_skill(home, "learned", "alpha")

    import shutil
    monkeypatch.setattr(shutil, "copytree", lambda *a, **k: None)  # copy nothing

    migrate_legacy_skills()

    assert (StackowlHome.workspace() / "skills" / "learned" / "alpha").exists(), (
        "the legacy tree was deleted despite nothing being copied"
    )


def test_migration_is_idempotent(home):
    _legacy_skill(home, "learned", "alpha")
    migrate_legacy_skills()
    migrate_legacy_skills()  # must not raise or lose anything
    assert (StackowlHome.skills_dir() / "learned" / "alpha" / "SKILL.md").exists()


def test_migration_is_a_noop_without_a_legacy_tree(home):
    migrate_legacy_skills()
    assert not (StackowlHome.workspace() / "skills").exists()


# --------------------------------------------------------------------------- #
# D05.1 (2026-08-30) — the invariant is now CHECKED and ENFORCED, not just
# checkable. It shipped on 2026-08-03 with a docstring claiming "the startup path
# asserts" it; measured 2026-08-30, its only references in src/ were that
# docstring and __all__. Nothing called it. These tests fail if that recurs.
# --------------------------------------------------------------------------- #


def test_boot_reports_the_invariant_on_the_HEALTHY_path(home, caplog):
    """INFO on success, not only on failure.

    Production runs at INFO, and an invariant that speaks only when broken can
    never be confirmed to have run — the D08.1 trap, where the sole evidence line
    was DEBUG and no volume of traffic could close the check.
    """
    import logging

    with caplog.at_level(logging.INFO):
        StackowlHome.ensure_exists()

    assert any(
        "skills tree is outside the model-writable workspace" in r.message
        for r in caplog.records
    ), "the healthy path must leave evidence that the check actually ran"


def test_boot_SHOUTS_when_the_trees_overlap(home, monkeypatch, caplog):
    """The pathological STACKOWL_DATA_DIR must produce an ERROR, not silence."""
    import logging

    monkeypatch.setenv("STACKOWL_DATA_DIR", str(home))
    with caplog.at_level(logging.INFO):
        StackowlHome.ensure_exists()

    assert any(
        r.levelno >= logging.ERROR and "SKILLS TREE IS INSIDE" in r.message
        for r in caplog.records
    )


def test_the_loader_REFUSES_to_exec_when_the_trees_overlap(home, monkeypatch, caplog):
    """The actuator, not just the alarm.

    Reporting the hole does not close it. This drives the real ``_load_tools``
    with a real .py file present and asserts nothing is executed — the file
    writes a marker on import, so an execution cannot hide.
    """
    import logging

    from stackowl.skills.loader import SkillLoader
    from stackowl.tools.registry import ToolRegistry

    monkeypatch.setenv("STACKOWL_DATA_DIR", str(home))
    tools_dir = home / "skills" / "evil" / "tools"
    tools_dir.mkdir(parents=True)
    marker = home / "EXECUTED"
    (tools_dir / "pwn.py").write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('pwned')\n"
    )

    loader = SkillLoader(tool_registry=ToolRegistry())
    with caplog.at_level(logging.INFO):
        names = loader._load_tools(tools_dir, "evil")

    assert names == ()
    assert not marker.exists(), "the module was EXECUTED despite the overlap"
    assert any("REFUSING to execute skill tool modules" in r.message for r in caplog.records)


def test_the_loader_still_execs_on_the_healthy_path(home, caplog):
    """The counterweight: a fence that always refuses is not a fence, it is an outage."""
    from stackowl.skills.loader import SkillLoader
    from stackowl.tools.registry import ToolRegistry

    tools_dir = StackowlHome.skills_dir() / "ok" / "tools"
    tools_dir.mkdir(parents=True)
    marker = home / "RAN"
    (tools_dir / "fine.py").write_text(
        f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n"
    )

    loader = SkillLoader(tool_registry=ToolRegistry())
    loader._load_tools(tools_dir, "ok")

    assert marker.exists(), "a legitimate skill tool module must still load"
