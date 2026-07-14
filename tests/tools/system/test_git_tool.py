"""GitTool — structured status/diff/commit/branch/worktree operations.

Runs against a REAL temp git repo (not a stub) since git's own plumbing output
is the thing being parsed — a mocked subprocess would only prove the parser
handles fixtures it wrote itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from stackowl.tools.system.git_tool import GitTool


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_status_clean_repo(repo: Path) -> None:
    result = await GitTool()(operation="status", repo=str(repo))
    assert result.success is True
    payload = json.loads(result.output)
    assert payload["clean"] is True
    assert payload["files"] == []


@pytest.mark.asyncio
async def test_status_reports_modified_and_untracked(repo: Path) -> None:
    (repo / "README.md").write_text("changed\n")
    (repo / "new.txt").write_text("new\n")

    result = await GitTool()(operation="status", repo=str(repo))

    payload = json.loads(result.output)
    assert payload["clean"] is False
    paths = {f["path"]: f for f in payload["files"]}
    assert paths["README.md"]["worktree_status"] == "M"
    assert paths["new.txt"]["untracked"] is True


@pytest.mark.asyncio
async def test_diff_reports_counts_and_bounded_text(repo: Path) -> None:
    (repo / "README.md").write_text("hello\nworld\n")

    result = await GitTool()(operation="diff", repo=str(repo))

    payload = json.loads(result.output)
    assert payload["files_changed"] == 1
    assert payload["insertions"] == 1
    assert "README.md" in payload["diff"]


@pytest.mark.asyncio
async def test_diff_truncates_to_max_chars(repo: Path) -> None:
    (repo / "README.md").write_text("x\n" * 5000)

    result = await GitTool()(operation="diff", repo=str(repo), max_chars=100)

    payload = json.loads(result.output)
    assert len(payload["diff"]) < 250
    assert "truncated" in payload["diff"]


@pytest.mark.asyncio
async def test_commit_stages_and_commits_named_paths(repo: Path) -> None:
    (repo / "new.txt").write_text("content\n")

    result = await GitTool()(
        operation="commit", repo=str(repo), message="add new.txt", paths=["new.txt"],
    )

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["summary"] == "add new.txt"
    assert payload["sha"]
    assert payload["files_changed"] == 1

    status = await GitTool()(operation="status", repo=str(repo))
    assert json.loads(status.output)["clean"] is True


@pytest.mark.asyncio
async def test_commit_with_nothing_staged_fails(repo: Path) -> None:
    result = await GitTool()(operation="commit", repo=str(repo), message="empty")

    assert result.success is False


@pytest.mark.asyncio
async def test_branch_create_and_list(repo: Path) -> None:
    create = await GitTool()(operation="branch", repo=str(repo), name="feature-x")
    assert create.success is True

    listed = await GitTool()(operation="branch", repo=str(repo))
    payload = json.loads(listed.output)
    names = {b["name"] for b in payload["branches"]}
    assert "feature-x" in names
    assert payload["current"] != "feature-x"  # not checked out (checkout defaults False)


@pytest.mark.asyncio
async def test_branch_delete(repo: Path) -> None:
    await GitTool()(operation="branch", repo=str(repo), name="throwaway")

    result = await GitTool()(operation="branch", repo=str(repo), name="throwaway", delete=True)

    assert result.success is True
    listed = await GitTool()(operation="branch", repo=str(repo))
    names = {b["name"] for b in json.loads(listed.output)["branches"]}
    assert "throwaway" not in names


@pytest.mark.asyncio
async def test_worktree_add_and_remove(repo: Path, tmp_path: Path) -> None:
    wt_path = tmp_path.parent / (tmp_path.name + "-wt")
    try:
        add_result = await GitTool()(
            operation="worktree_add", repo=str(repo), path=str(wt_path), new_branch="wt-branch",
        )
        assert add_result.success is True
        assert (wt_path / "README.md").exists()

        remove_result = await GitTool()(operation="worktree_remove", repo=str(repo), path=str(wt_path))
        assert remove_result.success is True
        assert not wt_path.exists()
    finally:
        if wt_path.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=repo)


@pytest.mark.asyncio
async def test_unknown_operation_refused(repo: Path) -> None:
    result = await GitTool()(operation="push", repo=str(repo))

    assert result.success is False
    assert result.side_effect_committed is False


@pytest.mark.asyncio
async def test_status_on_non_repo_fails_structured(tmp_path: Path) -> None:
    result = await GitTool()(operation="status", repo=str(tmp_path))

    assert result.success is False
