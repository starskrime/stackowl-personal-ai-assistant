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
