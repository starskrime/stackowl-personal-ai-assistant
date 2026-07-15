"""End-to-end: a 2-story epic (one independent, one dependent) from
ObjectiveStore creation through ObjectiveDriverHandler ticks to a merged,
ready-to-merge objective. Real git repos throughout — only the `claude`
binary is stubbed (matching every other test in this codebase's
git_tool/claude_code test style, see tests/objectives/test_epic_runner.py)."""

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
from stackowl.objectives.driver import ObjectiveDriverHandler
from stackowl.objectives.model import Objective, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
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


def _stub_claude(tmp_path: Path) -> Path:
    stub = tmp_path / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        # Content must differ per invocation: story b's worktree branches from
        # the integration branch AFTER story a has already merged, so writing
        # identical bytes to the same filename produces no diff — git sees a
        # clean worktree and epic_runner reports "claude_code made no changes".
        "echo done-$$-$(date +%s%N) > story_output.txt\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "s"}\'\n'
    )
    os.chmod(stub, 0o755)
    return stub


async def test_two_story_epic_reaches_ready_to_merge(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: DbPool,
) -> None:
    stub = _stub_claude(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    # Epic stories bypass _run_subgoal/backend.run entirely (they call
    # epic_runner.run_story directly via _advance_epic) — backend=None is
    # valid here since ObjectiveDriverHandler's `assert self._backend is not
    # None` in _run_subgoal is only reached by the PLAIN-objective path
    # (driver.py's _advance falls through to it only when objective.repo is
    # unset), never by _advance_epic (confirmed against the actual Task 8/9
    # implementation: _advance_epic calls epic_runner.run_story exclusively).
    driver = ObjectiveDriverHandler(db=pool, backend=None)
    store = ObjectiveStore(pool, "default")

    objective_id = "obj-e2e-1"
    integration_branch = f"stackowl/epic-{objective_id}"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)
    objective = Objective(
        objective_id=objective_id, owner_id="default", intent="e2e epic",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    specs = [SubgoalSpec(description="story a"), SubgoalSpec(description="story b", depends_on=[0])]
    await store.add_subgoals(objective_id, specs)

    # Tick until settled (bounded — never an unbounded loop in a test). Each
    # story's own worktree creation + claude_code + `uv run pytest -q` (twice
    # — story worktree, then re-verified on the merged integration branch)
    # takes ~6s wall clock (measured via test_epic_runner.py's single-story
    # equivalent); story b only starts once story a is done (depends_on=[0]),
    # so two sequential real merges need well over the naive default budget.
    for _ in range(150):
        await driver._advance(store, await store.get(objective_id))
        await asyncio.sleep(0.4)
        current = await store.get(objective_id)
        if current.status == "blocked" and current.blocker == "awaiting merge confirm":
            break
    else:
        pytest.fail("epic never reached ready-to-merge within the bounded tick loop")

    subgoals = await store.list_subgoals(objective_id)
    assert all(sg.status == "done" for sg in subgoals)
