"""epic_runner — per-story background sequence (Task #4/7).

Real git + real subprocess throughout (no mocking of git or the filesystem):
a temp repo is `git init`'d and shelled out to for every assertion, and the
``claude`` binary is a real executable shell-script stub on PATH (via
``shutil.which`` monkeypatched) rather than a mocked ToolResult — this is the
only way to prove the actual GitTool/ClaudeCodeTool/RunTestsTool integration
works, not just that epic_runner calls them with the right-shaped arguments.

Uses the SAME ``pool``/``STACKOWL_HOME``/``TestModeGuard`` fixtures/patterns as
tests/objectives/test_objective_store.py and tests/tools/system/test_claude_code.py
(there is no ``db_pool`` fixture in this repo — the real name is ``pool``).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.objectives.epic_runner import run_story
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore
from stackowl.paths import StackowlHome


def _init_repo(path: Path) -> None:
    """A REAL pytest test file is committed so `run_story`'s `uv run pytest -q`
    steps (story worktree AND merged integration branch) exercise the ACTUAL
    RunTestsTool/subprocess integration rather than a synthetic ToolResult —
    an empty repo makes pytest exit "no tests ran" (all_passed=False), which
    would falsely look like a story-test failure."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x\n")
    (path / "test_sample.py").write_text("def test_sample() -> None:\n    assert 1 == 1\n")
    subprocess.run(["git", "add", "f.txt", "test_sample.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _init_repo(r)
    return r


@pytest.fixture(autouse=True)
def _isolated_stackowl_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`epic_runner` mints worktrees under `StackowlHome.worktrees_dir()` — keep
    every test's scratch worktrees inside tmp_path, never the real ~/.stackowl."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(tmp_path / "home" / "workspace"))


@pytest.fixture
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "objectives.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _stub_claude(tmp_path: Path, *, writes_file: bool = True) -> Path:
    stub = tmp_path / "claude"
    body = "#!/bin/sh\n"
    if writes_file:
        body += "echo done > story_output.txt\n"
    body += (
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "s"}\'\n'
    )
    stub.write_text(body)
    os.chmod(stub, 0o755)
    return stub


@pytest.mark.asyncio
async def test_run_story_clean_merge_marks_done(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: DbPool,
) -> None:
    integration_branch = "stackowl/epic-test"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)

    store = ObjectiveStore(pool, "default")
    objective = Objective(
        objective_id="obj-1", owner_id="default", intent="test",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    [subgoal] = await store.add_subgoals("obj-1", ["do the thing"])

    stub = _stub_claude(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    locks: dict[str, asyncio.Lock] = {}
    await run_story(objective, subgoal, store, locks)

    reloaded = (await store.list_subgoals("obj-1"))[0]
    assert reloaded.status == "done", reloaded.result
    log_output = subprocess.run(
        ["git", "log", integration_branch, "--oneline"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout
    # init commit + the story's own commit + the --no-ff merge commit.
    assert len(log_output.splitlines()) >= 3
    # `ObjectiveStore.update_subgoal`'s `worktree_path` param follows an
    # "only written when supplied" convention (None ⇒ leave unchanged, not
    # ⇒ clear to NULL — see store.py) so the DB row may still carry the
    # LAST worktree_path epic_runner recorded even after a clean done; what
    # matters is that the directory itself is actually gone.
    assert reloaded.worktree_path is None or not Path(reloaded.worktree_path).exists()
    # Neither the deterministic story worktree nor claude_code's nested
    # isolation worktree survive a clean, merged-and-verified run.
    assert not (StackowlHome.worktrees_dir()).exists() or not any(
        StackowlHome.worktrees_dir().iterdir()
    )


@pytest.mark.asyncio
async def test_run_story_claude_unavailable_blocks_story(
    repo: Path, monkeypatch: pytest.MonkeyPatch, pool: DbPool,
) -> None:
    integration_branch = "stackowl/epic-test2"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)
    store = ObjectiveStore(pool, "default")
    objective = Objective(
        objective_id="obj-2", owner_id="default", intent="test",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    [subgoal] = await store.add_subgoals("obj-2", ["do the thing"])

    monkeypatch.setattr(shutil, "which", lambda name: None)  # no claude binary
    locks: dict[str, asyncio.Lock] = {}
    await run_story(objective, subgoal, store, locks)

    reloaded = (await store.list_subgoals("obj-2"))[0]
    assert reloaded.status in ("pending", "blocked")  # retried or escalated, never silently "done"


@pytest.mark.asyncio
async def test_run_story_retry_does_not_collide_on_worktree_recreate(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: DbPool,
) -> None:
    """The retry-worktree-collision question: `_retry_or_block` leaves the
    subgoal `pending` without cleaning up — a SECOND run_story call for the
    SAME subgoal_id must not fail because add_worktree collides with what the
    first (failed) attempt already created at the deterministic
    `stackowl/story-<subgoal_id>` path/branch."""
    integration_branch = "stackowl/epic-test3"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)
    store = ObjectiveStore(pool, "default")
    objective = Objective(
        objective_id="obj-3", owner_id="default", intent="test",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    [subgoal] = await store.add_subgoals("obj-3", ["do the thing"])

    # First attempt: claude binary missing → claude_code fails → retry (pending).
    monkeypatch.setattr(shutil, "which", lambda name: None)
    locks: dict[str, asyncio.Lock] = {}
    await run_story(objective, subgoal, store, locks)
    after_first = (await store.list_subgoals("obj-3"))[0]
    assert after_first.status == "pending"
    assert after_first.attempts == 1

    # Second attempt (the "retry"): claude now available and succeeds — must
    # NOT fail with a worktree/branch-already-exists error.
    stub = _stub_claude(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)
    await run_story(objective, after_first, store, locks)

    after_second = (await store.list_subgoals("obj-3"))[0]
    assert after_second.status == "done", after_second.result
