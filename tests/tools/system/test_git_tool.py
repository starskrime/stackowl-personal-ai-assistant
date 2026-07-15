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

from stackowl.tools.system.git_tool import GitTool, diff_summary


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


@pytest.mark.asyncio
async def test_diff_summary_direct_call(repo: Path) -> None:
    (repo / "README.md").write_text("changed\n")
    result = await diff_summary(str(repo))
    assert result.success is True
    payload = json.loads(result.output)
    assert payload["files_changed"] == 1
    assert payload["insertions"] >= 1
    assert payload["files"][0]["path"] == "README.md"


@pytest.mark.asyncio
async def test_diff_summary_non_repo_returns_failed_result(tmp_path: Path) -> None:
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    result = await diff_summary(str(non_repo))
    assert result.success is False


@pytest.mark.asyncio
async def test_diff_summary_includes_untracked_new_file(repo: Path) -> None:
    """Plain `git diff` never reports untracked files (git's own design) —
    diff_summary must still surface them, or a brand-new file an agent
    creates would silently vanish from the "what really changed" check."""
    (repo / "new_module.py").write_text("def hello():\n    return 1\n")
    result = await diff_summary(str(repo))
    assert result.success is True
    payload = json.loads(result.output)
    entry = next(f for f in payload["files"] if f["path"] == "new_module.py")
    assert entry["status"] == "untracked"
    assert entry["insertions"] == 2
    assert "new_module.py" in payload["diff"]
    assert "+def hello():" in payload["diff"]


@pytest.mark.asyncio
async def test_diff_summary_marks_tracked_entries_modified(repo: Path) -> None:
    (repo / "README.md").write_text("changed\n")
    result = await diff_summary(str(repo))
    payload = json.loads(result.output)
    entry = next(f for f in payload["files"] if f["path"] == "README.md")
    assert entry["status"] == "modified"


@pytest.mark.asyncio
async def test_diff_summary_scoped_paths_ignores_untracked_files_outside_scope(repo: Path) -> None:
    (repo / "README.md").write_text("changed\n")
    (repo / "unrelated_new_file.py").write_text("x = 1\n")
    result = await diff_summary(str(repo), paths=["README.md"])
    assert result.success is True
    payload = json.loads(result.output)
    paths = {f["path"] for f in payload["files"]}
    assert paths == {"README.md"}


@pytest.mark.asyncio
async def test_diff_summary_untracked_binary_file_counted_not_diffed(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(b"\x00\x01\xff\xfe binary content \x00")
    result = await diff_summary(str(repo))
    payload = json.loads(result.output)
    entry = next(f for f in payload["files"] if f["path"] == "blob.bin")
    assert entry["binary"] is True
    assert entry["insertions"] == 0
    assert "blob.bin" not in payload["diff"]


@pytest.mark.asyncio
async def test_diff_summary_staged_skips_untracked_scan(repo: Path) -> None:
    (repo / "new_module.py").write_text("def hello():\n    return 1\n")
    result = await diff_summary(str(repo), staged=True)
    assert result.success is True
    payload = json.loads(result.output)
    assert payload["files_changed"] == 0  # nothing staged, and untracked scan is skipped for staged=True


@pytest.mark.asyncio
async def test_diff_summary_oversized_untracked_file_not_read_into_memory(repo: Path) -> None:
    """The size cap is checked via stat() BEFORE read_bytes() — a huge
    untracked file must never be fully read into memory just to be
    discarded. Assert on the OBSERVABLE behavior (note present, no diff
    text, still counted) since the memory-safety property itself isn't
    directly assertable from a test, only the outcome that depends on it."""
    big = repo / "huge.bin"
    big.write_bytes(b"x" * 200_000)  # over _MAX_UNTRACKED_FILE_BYTES (100_000)
    result = await diff_summary(str(repo))
    payload = json.loads(result.output)
    entry = next(f for f in payload["files"] if f["path"] == "huge.bin")
    assert entry["note"] == "too large to diff"
    assert entry["insertions"] == 0
    assert "huge.bin" not in payload["diff"]


@pytest.mark.asyncio
async def test_git_diff_operation_includes_untracked_file(repo: Path) -> None:
    """End-to-end at the Tool-callable level (not just the free function) —
    GitTool's own `diff` operation inherits the untracked-file coverage
    transparently since `_diff` now delegates to `diff_summary`."""
    (repo / "new_module.py").write_text("def hello():\n    return 1\n")
    result = await GitTool()(operation="diff", repo=str(repo))
    assert result.success is True
    payload = json.loads(result.output)
    entry = next(f for f in payload["files"] if f["path"] == "new_module.py")
    assert entry["status"] == "untracked"


@pytest.mark.asyncio
async def test_current_branch_returns_checked_out_branch(repo: Path) -> None:
    from stackowl.tools.system.git_tool import current_branch

    branch = await current_branch(str(repo))
    assert branch in ("main", "master")  # git init's default varies by config
