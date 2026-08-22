"""WorkspaceEnvJanitor — the decay leg for per-tool virtualenv sprawl.

Bakir, 2026-08-22: "each tool creates his env instead of using one centralized
env." Measured that morning: FOUR virtualenvs in the workspace — genv 344 MB,
venv 149 MB, gmail_env 149 MB, .venv 65 MB — 707 MB on a root filesystem with
1.3 GB free, two of them with byte-identical 24-package sets, and NOT ONE
referenced anywhere in src/.

The centralised env is the fix; this is the leg that stops it regrowing. The
tests below pin the three things that make it safe to run unattended: it finds a
stray env structurally, it REFUSES to touch the env everything runs from, and it
judges on USE rather than age.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.scheduler.handlers.workspace_env_janitor import (
    WorkspaceEnvJanitorHandler,
    find_stray_envs,
)
from stackowl.scheduler.job import Job


def _job(**params: object) -> Job:
    return Job(
        job_id=f"workspace_env_janitor-{uuid.uuid4().hex[:6]}",
        handler_name="workspace_env_janitor",
        schedule="every 24h",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=dict(params),
    )


def _backdate(root: Path, *, days: float) -> None:
    """Age a whole tree, the directory itself included.

    Separate from `_make_venv` because anything created INSIDE a directory
    updates that directory's mtime, and `_last_touched` counts it — so a test that
    backdates first and then adds a file is testing a fresh env, not an idle one.
    """
    import os
    old = time.time() - (days * 86_400)
    for path in [*root.rglob("*"), root]:
        try:
            os.utime(path, (old, old), follow_symlinks=False)
        except (OSError, NotImplementedError):
            continue


def _make_venv(root: Path, name: str, *, idle_days: float = 0.0) -> Path:
    """A structurally real virtualenv — pyvenv.cfg plus an interpreter.

    Named freely on purpose: the four on the live box were `genv`, `venv`,
    `gmail_env` and `.venv`, so anything keyed on the NAME would have missed
    whichever one came next.
    """
    env = root / name
    (env / "bin").mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.14.5\n")
    (env / "bin" / "python").write_text("#!/bin/sh\n")
    (env / "lib").mkdir()
    (env / "lib" / "package.py").write_text("x" * 4096)
    if idle_days:
        _backdate(env, days=idle_days)
    return env


def test_it_finds_a_stray_env_by_STRUCTURE_not_by_name(tmp_path: Path) -> None:
    """A name list would have missed the next one — there were four spellings."""
    shared = _make_venv(tmp_path, "env")
    for name in ("genv", "gmail_env", ".venv", "some_other_env"):
        _make_venv(tmp_path, name, idle_days=30)
    (tmp_path / "downloads").mkdir()  # not a venv — must be ignored

    stray = find_stray_envs(tmp_path, shared, max_idle_days=14)

    assert {p.name for p, _ in stray} == {"genv", "gmail_env", ".venv", "some_other_env"}


def test_it_NEVER_removes_the_shared_env(tmp_path: Path) -> None:
    """The worst available outcome, pinned explicitly.

    The shared env is the one everything installs into and runs from. Deleting it
    would be far worse than the 707 MB this exists to reclaim, and it is old and
    idle exactly like a stray one — so age alone must never be what saves it.
    """
    shared = _make_venv(tmp_path, "env", idle_days=365)

    stray = find_stray_envs(tmp_path, shared, max_idle_days=14)

    assert stray == [], f"the SHARED env was selected for deletion: {stray}"


def test_an_env_still_in_USE_is_left_alone(tmp_path: Path) -> None:
    """Judged on the newest mtime in the tree, not the directory's own.

    A venv's directory mtime stops moving the moment it is built, while the env
    goes on being imported from every day. Judging on the directory would reclaim
    environments in active use — a janitor causing the outage it prevents.
    """
    shared = _make_venv(tmp_path, "env")
    active = _make_venv(tmp_path, "active_env", idle_days=90)
    # one file touched recently, as an import or install would leave it
    (active / "lib" / "package.py").touch()

    stray = find_stray_envs(tmp_path, shared, max_idle_days=14)

    assert active not in [p for p, _ in stray], (
        "an env touched today was judged stray — the sweep is reading the "
        "directory mtime instead of the tree"
    )


def test_a_recently_built_env_is_left_alone(tmp_path: Path) -> None:
    """The other jaw: a tool that just built an isolated env keeps it long enough
    to finish the job it built it for."""
    shared = _make_venv(tmp_path, "env")
    _make_venv(tmp_path, "fresh_env")

    assert find_stray_envs(tmp_path, shared, max_idle_days=14) == []


@pytest.mark.asyncio
async def test_the_handler_actually_frees_the_bytes(tmp_path: Path) -> None:
    """MEASURE THE EFFECT. Not "it reported success" — the directory is gone."""
    shared = _make_venv(tmp_path, "env")
    doomed = _make_venv(tmp_path, "genv", idle_days=30)

    result = await WorkspaceEnvJanitorHandler(tmp_path, shared).execute(_job())

    assert result.success, result.error
    assert result.metadata["envs_removed"] == 1, result.metadata
    assert result.metadata["freed_bytes"] > 0, result.metadata
    assert not doomed.exists(), "reported removed, but the directory is still there"
    assert shared.exists(), "the shared env was destroyed"


@pytest.mark.asyncio
async def test_a_clean_workspace_is_a_quiet_no_op(tmp_path: Path) -> None:
    """Nothing stray must not look like a failure, and must not log an alarm —
    a janitor that cries wolf on a healthy box trains its reader to ignore it."""
    shared = _make_venv(tmp_path, "env")

    result = await WorkspaceEnvJanitorHandler(tmp_path, shared).execute(_job())

    assert result.success
    assert result.metadata["envs_removed"] == 0


@pytest.mark.asyncio
async def test_a_missing_workspace_never_raises(tmp_path: Path) -> None:
    """Self-healing contract: a sweep must never take the scheduler down."""
    missing = tmp_path / "nope"
    result = await WorkspaceEnvJanitorHandler(missing, missing / "env").execute(_job())
    assert result.success
    assert result.metadata["envs_removed"] == 0


def test_a_symlinked_dir_is_not_counted_TWICE(tmp_path: Path) -> None:
    """Found by running the janitor against the live box, not by review.

    The stray `source` env there carries `lib64 -> lib`, which every virtualenv on
    Linux does. `Path.rglob` descends into symlinked directories, so the library
    tree was walked twice and the sweep reported 104 MB for a directory `du`
    measures at 14 MB — a janitor claiming bytes it never freed, which is the
    "trust the call instead of the effect" defect wearing this codebase's own
    uniform.

    Following links would also let the walk escape the tree entirely.
    """
    shared = _make_venv(tmp_path, "env")
    stray = _make_venv(tmp_path, "linky")
    (stray / "lib64").symlink_to(stray / "lib")  # exactly what a real venv has
    _backdate(stray, days=30)  # AFTER the symlink — creating it touches the dir

    found = dict((p.name, size) for p, size in find_stray_envs(tmp_path, shared, 14))

    real_bytes = sum(
        f.stat().st_size for f in (stray / "lib").iterdir() if f.is_file()
    ) + (stray / "pyvenv.cfg").stat().st_size + (stray / "bin" / "python").stat().st_size
    assert found["linky"] == real_bytes, (
        f"reported {found['linky']} bytes but only {real_bytes} exist — the walk "
        "is following lib64 -> lib and counting the tree twice"
    )


@pytest.mark.asyncio
async def test_a_symlinked_env_deletes_the_LINK_not_the_target(tmp_path: Path) -> None:
    """A compatibility alias must never take the real env down with it.

    On the live box `gmail_env` is now a symlink to the shared `env`, kept so that
    126 rows of stored facts, lessons and prompts referencing the old path keep
    working. `find_stray_envs` resolves before comparing, so the alias is
    recognised AS the shared env and skipped — asserted here because the failure
    mode is destroying the environment everything runs from.
    """
    shared = _make_venv(tmp_path, "env")
    alias = tmp_path / "gmail_env"
    alias.symlink_to(shared)

    result = await WorkspaceEnvJanitorHandler(tmp_path, shared).execute(_job())

    assert result.metadata["envs_removed"] == 0, "the alias was treated as a stray"
    assert shared.exists() and (shared / "pyvenv.cfg").is_file()
    assert alias.is_symlink()
