"""ClaudeCodeTool — headless Claude Code CLI subprocess wrapper.

Covers: binary-missing self-heal (never a silent no-op), empty-prompt refusal,
SEC-3 child-exclusion self-defense (delegation_depth>0), and a successful run
against a stub ``claude`` binary that mimics ``--output-format json``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.tools.child_exclusion import CHILD_EXCLUDED_TOOLS
from stackowl.tools.system.claude_code import ClaudeCodeTool


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def _stub_claude_binary(tmp_path: Path) -> Path:
    stub = tmp_path / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "sess-123"}\'\n'
    )
    os.chmod(stub, 0o755)
    return stub


@pytest.mark.asyncio
async def test_unavailable_when_claude_not_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No 'claude' binary on PATH ⇒ structured unavailable, never a raise/no-op."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = await ClaudeCodeTool()(prompt="fix the bug", workdir=str(tmp_path))

    assert result.success is False
    assert result.side_effect_committed is False
    assert "not installed" in (result.error or "").lower() or "path" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_empty_prompt_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ClaudeCodeTool()(prompt="   ", workdir=str(tmp_path))

    assert result.success is False
    assert result.side_effect_committed is False


def test_canonical_child_exclusion_set_contains_claude_code() -> None:
    assert "claude_code" in CHILD_EXCLUDED_TOOLS


@pytest.mark.asyncio
async def test_refuses_for_delegated_child() -> None:
    """F163-style self-defense — a delegated sub-agent (depth>0) is refused."""
    token = TraceContext.start(delegation_depth=1)
    try:
        result = await ClaudeCodeTool().execute(prompt="fix the bug")
    finally:
        TraceContext.reset(token)

    assert result.success is False
    assert result.side_effect_committed is False
    assert "deleg" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_successful_run_parses_json_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stub 'claude' binary prints Claude Code's --output-format json shape."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    stub = tmp_path / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "sess-123"}\'\n'
    )
    os.chmod(stub, 0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(prompt="fix the bug", workdir=str(tmp_path))

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["is_error"] is False
    assert payload["session_id"] == "sess-123"


@pytest.mark.asyncio
async def test_isolates_into_worktree_when_workdir_is_git_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A git-repo workdir gets isolated into a scratch worktree, never run
    directly on the repo's checked-out branch, and the run's edits land there."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    stub = repo / "claude_stub"
    stub.write_text(
        "#!/bin/sh\n"
        "echo edited > isolated.txt\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "sess-999"}\'\n'
    )
    os.chmod(stub, 0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(prompt="add a file", workdir=str(repo))

    assert result.success is True
    payload = json.loads(result.output)
    isolation = payload["isolation"]
    assert isolation["isolated"] is True
    assert isolation["base_repo"] == str(repo)
    worktree_path = Path(isolation["worktree_path"])
    assert worktree_path != repo
    assert worktree_path.is_relative_to(StackowlHome.worktrees_dir())

    # The claude stub's edit landed in the WORKTREE, not the original repo.
    assert (worktree_path / "isolated.txt").exists()
    assert not (repo / "isolated.txt").exists()

    # The scratch branch exists in the repo but was never checked out there.
    branches = subprocess.run(
        ["git", "branch", "--list", isolation["branch"]],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert isolation["branch"] in branches
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert current != isolation["branch"]


@pytest.mark.asyncio
async def test_resume_skips_isolation_even_in_git_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """resume_session_id must see the SAME files an earlier turn edited — no
    session→worktree mapping exists yet, so isolation is skipped, not guessed."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    stub = _stub_claude_binary(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(
        prompt="continue", workdir=str(repo), resume_session_id="sess-123",
    )

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["isolation"]["isolated"] is False


@pytest.mark.asyncio
async def test_non_git_workdir_skips_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    stub = _stub_claude_binary(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(prompt="fix the bug", workdir=str(tmp_path))

    payload = json.loads(result.output)
    assert payload["isolation"]["isolated"] is False


@pytest.mark.asyncio
async def test_successful_run_in_git_repo_includes_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A git-repo workdir gets a real `git diff` folded into the JSON payload
    under "diff" — independent confirmation of what changed, not just the
    CLI's own self-reported result."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    stub = repo / "claude_stub"
    stub.write_text(
        "#!/bin/sh\n"
        "echo edited >> README.md\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "sess-999"}\'\n'
    )
    os.chmod(stub, 0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(prompt="add a file", workdir=str(repo))

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["diff"]["files_changed"] == 1
    assert payload["diff"]["files"][0]["path"] == "README.md"


@pytest.mark.asyncio
async def test_successful_run_in_non_git_workdir_has_no_diff_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-git workdir: today's behavior unchanged — no "diff" key at all."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    stub = tmp_path / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "sess-123"}\'\n'
    )
    os.chmod(stub, 0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(prompt="fix the bug", workdir=str(tmp_path))

    assert result.success is True
    payload = json.loads(result.output)
    assert "diff" not in payload
