# Observable Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a successful `claude_code`/`apply_patch`/`edit` call, fold a real `git diff` (files changed, +/- counts, bounded unified diff) into that tool's `ToolResult.output` when the target is a git repo — independent confirmation of what changed, not just each tool's own self-computed summary.

**Architecture:** Extract `GitTool._diff`'s body into a new module-level `diff_summary()` free function in `git_tool.py` (same pattern as the existing `is_git_repo`/`add_worktree` helpers — callable without the full `Tool.__call__` consent/logging wrapper). `GitTool._diff` becomes a thin wrapper around it (zero behavior change to `GitTool` itself). `claude_code`/`apply_patch`/`edit` call `is_git_repo()` + `diff_summary()` directly as a best-effort post-step; any failure is logged and the diff block is simply omitted, never fails the parent call.

**Tech Stack:** Python 3.13, existing `run_argv` subprocess seam, `pytest` + `pytest-asyncio`.

## Global Constraints

- No `ToolResult` schema change — diff text folds into the existing `.output` field.
- Non-git targets: zero behavior change (verified by existing tests staying green).
- `diff_summary()` never raises; a failure returns a failed `ToolResult`, never propagates as an exception.
- Minimal diff — touch only the exact lines needed in each file.
- Full spec: `docs/superpowers/specs/2026-07-14-observable-diff-design.md`.

---

### Task 1: Extract `diff_summary()` free function in `git_tool.py`

**Files:**
- Modify: `src/stackowl/tools/system/git_tool.py`
- Test: `tests/tools/system/test_git_tool.py`

**Interfaces:**
- Produces: `diff_summary(repo: str, *, staged: bool = False, paths: list[str] | None = None, max_chars: int = _DEFAULT_MAX_DIFF_CHARS) -> ToolResult` — module-level, exported in `__all__`. On success, `.output` is a JSON string shaped `{"files_changed": int, "insertions": int, "deletions": int, "files": [{"path", "insertions", "deletions", "binary"}], "diff": str}`. On failure (not a repo, git error), returns the failed `ToolResult` from the underlying `run_argv` call (never raises) — callers doing a best-effort append check `.success` and omit on `False`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/tools/system/test_git_tool.py` (after the existing imports, using the file's existing `repo` fixture):

```python
from stackowl.tools.system.git_tool import GitTool, diff_summary


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tools/system/test_git_tool.py -k diff_summary -v`
Expected: FAIL with `ImportError: cannot import name 'diff_summary'`.

- [ ] **Step 3: Add `diff_summary()` and refactor `GitTool._diff` to use it**

In `src/stackowl/tools/system/git_tool.py`, change the `__all__` line (currently `__all__ = ["GitTool", "add_worktree", "is_git_repo"]`) to:

```python
__all__ = ["GitTool", "add_worktree", "diff_summary", "is_git_repo"]
```

Add this new function immediately after `_truncate` (which ends at line 118) and before `is_git_repo` (which starts at line 121):

```python
async def diff_summary(
    repo: str, *, staged: bool = False, paths: list[str] | None = None,
    max_chars: int = _DEFAULT_MAX_DIFF_CHARS,
) -> ToolResult:
    """Structured git diff (files_changed/insertions/deletions/files/diff, as
    JSON in ``.output``) as a :class:`ToolResult` — never raises.

    Module-level (not a GitTool method) so a caller that needs git diff info
    without the full Tool wrapper — e.g. claude_code/apply_patch/edit's
    post-edit "here's what really changed" append — can call it directly
    (mirroring ``is_git_repo``/``add_worktree`` above). On failure (not a
    repo, git error), returns the failed ``ToolResult`` from the underlying
    ``run_argv`` call unchanged; a best-effort caller checks ``.success`` and
    omits the diff rather than propagating the failure.
    """
    base = ["git", "diff"] + (["--cached"] if staged else [])
    path_args = (["--"] + paths) if paths else []
    stat_result = await run_argv(base + ["--numstat"] + path_args, tool_name="git", workdir=repo, intent="read")
    if not stat_result.success:
        return stat_result
    insertions, deletions, files = _parse_numstat(stat_result.output)

    text_result = await run_argv(base + path_args, tool_name="git", workdir=repo, intent="read")
    if not text_result.success:
        return text_result

    parsed = {
        "files_changed": len(files),
        "insertions": insertions,
        "deletions": deletions,
        "files": files,
        "diff": _truncate(text_result.output, max_chars),
    }
    duration_ms = stat_result.duration_ms + text_result.duration_ms
    log.tool.debug(
        "git.diff_summary: exit",
        extra={"_fields": {"success": True, "duration_ms": duration_ms}},
    )
    return ToolResult(
        success=True, output=json.dumps(parsed, ensure_ascii=False),
        duration_ms=duration_ms, side_effect_committed=False,
    )
```

Replace the existing `_diff` method body (currently lines 313-342) with:

```python
    async def _diff(self, repo: str, kwargs: dict[str, object], t0: float) -> ToolResult:
        staged = bool(kwargs.get("staged", False))
        paths_raw = kwargs.get("paths")
        paths = [str(p) for p in paths_raw] if isinstance(paths_raw, list) else None
        max_chars_raw = kwargs.get("max_chars")
        try:
            max_chars = int(max_chars_raw) if max_chars_raw else _DEFAULT_MAX_DIFF_CHARS  # type: ignore[call-overload]
        except (TypeError, ValueError):
            max_chars = _DEFAULT_MAX_DIFF_CHARS
        return await diff_summary(repo, staged=staged, paths=paths, max_chars=max_chars)
```

Note: `t0` becomes an unused parameter of `_diff` after this change (kept for call-site signature compatibility with the other `_<operation>` methods in the dispatch table at `execute()`'s `if operation == "diff": return await self._diff(repo, kwargs, t0)` line — do not change that call site).

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/tools/system/test_git_tool.py -k diff_summary -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing GitTool test file to confirm zero regression**

Run: `uv run pytest tests/tools/system/test_git_tool.py -v`
Expected: all pass (existing `diff`-operation tests must be byte-identical in behavior since `_diff` now delegates to `diff_summary`).

- [ ] **Step 6: Gate**

Run: `uv run ruff check src/stackowl/tools/system/git_tool.py && uv run mypy src/stackowl/tools/system/git_tool.py`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/tools/system/git_tool.py tests/tools/system/test_git_tool.py
git commit -m "refactor(tools): extract diff_summary() free function from GitTool._diff"
```

---

### Task 2: `claude_code` — fold real diff into the JSON payload

**Files:**
- Modify: `src/stackowl/tools/system/claude_code.py`
- Test: `tests/tools/system/test_claude_code.py`

**Interfaces:**
- Consumes: `diff_summary(repo: str) -> ToolResult` and `is_git_repo(path: str) -> bool` (Task 1, both already importable from `stackowl.tools.system.git_tool`).
- Produces: no new public interface — `ClaudeCodeTool`'s existing JSON output payload gains an optional `"diff"` key (a dict shaped as `diff_summary`'s parsed JSON) when `workdir` (the actual directory the run executed in, post worktree-isolation) is a git repo.

- [ ] **Step 1: Write the failing test**

Add to `tests/tools/system/test_claude_code.py` (the file already defines `_init_repo` at module level and imports `json`/`os`/`shutil`/`subprocess`/`Path`/`pytest`/`TestModeGuard`/`StackowlHome`/`ClaudeCodeTool`):

```python
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
        "echo edited > isolated.txt\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "sess-999"}\'\n'
    )
    os.chmod(stub, 0o755)
    monkeypatch.setattr(shutil, "which", lambda name: str(stub) if name == "claude" else None)

    result = await ClaudeCodeTool()(prompt="add a file", workdir=str(repo))

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["diff"]["files_changed"] == 1
    assert payload["diff"]["files"][0]["path"] == "isolated.txt"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tools/system/test_claude_code.py -k "includes_diff or no_diff_key" -v`
Expected: FAIL — `test_successful_run_in_git_repo_includes_diff` fails on `payload["diff"]` (`KeyError`); `test_successful_run_in_non_git_workdir_has_no_diff_key` currently PASSES already (no code changed yet) — that's fine, it locks in the baseline before Step 3 changes anything.

- [ ] **Step 3: Implement**

In `src/stackowl/tools/system/claude_code.py`, change the import line (currently `from stackowl.tools.system.git_tool import add_worktree, is_git_repo`) to:

```python
from stackowl.tools.system.git_tool import add_worktree, diff_summary, is_git_repo
```

Replace the EXIT block (currently lines 251-266):

```python
        # 4. EXIT — fold Claude Code's own JSON summary + isolation info into the output.
        parsed = self._parse_output(result.output)
        parsed = {**parsed, "isolation": isolation} if isinstance(parsed, dict) else {
            "result": parsed, "isolation": isolation,
        }
        log.tool.debug(
            "claude_code.execute: exit",
            extra={
                "_fields": {
                    "success": True,
                    "is_error": parsed.get("is_error"),
                    "duration_ms": result.duration_ms,
                }
            },
        )
        return result.model_copy(update={"output": json.dumps(parsed, ensure_ascii=False)})
```

with:

```python
        # 4. EXIT — fold Claude Code's own JSON summary + isolation info into the output.
        parsed = self._parse_output(result.output)
        parsed = {**parsed, "isolation": isolation} if isinstance(parsed, dict) else {
            "result": parsed, "isolation": isolation,
        }
        # Independent confirmation of what changed, not just the CLI's own
        # self-report — best-effort: any failure is logged and omitted, never
        # fails this already-successful call (research artifact §3 proposal 4).
        if await is_git_repo(workdir):
            diff_result = await diff_summary(workdir)
            if diff_result.success:
                try:
                    parsed["diff"] = json.loads(diff_result.output)
                except (json.JSONDecodeError, ValueError) as exc:
                    log.tool.warning(
                        "claude_code.execute: diff_summary output was not valid JSON — omitting",
                        exc_info=exc,
                    )
            else:
                log.tool.debug(
                    "claude_code.execute: diff_summary failed — omitting diff",
                    extra={"_fields": {"error": diff_result.error}},
                )
        log.tool.debug(
            "claude_code.execute: exit",
            extra={
                "_fields": {
                    "success": True,
                    "is_error": parsed.get("is_error"),
                    "duration_ms": result.duration_ms,
                }
            },
        )
        return result.model_copy(update={"output": json.dumps(parsed, ensure_ascii=False)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tools/system/test_claude_code.py -v`
Expected: all pass (the two new tests plus every pre-existing test in the file, unchanged).

- [ ] **Step 5: Gate**

Run: `uv run ruff check src/stackowl/tools/system/claude_code.py && uv run mypy src/stackowl/tools/system/claude_code.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/tools/system/claude_code.py tests/tools/system/test_claude_code.py
git commit -m "feat(tools): claude_code folds a real git diff into its JSON payload"
```

---

### Task 3: `apply_patch` — append real diff after the self-computed one

**Files:**
- Modify: `src/stackowl/tools/io/apply_patch.py`
- Test: `tests/tools/io/test_apply_patch.py`

**Interfaces:**
- Consumes: `diff_summary(repo: str) -> ToolResult`, `is_git_repo(path: str) -> bool` (Task 1), `data_root() -> Path` (already available via `stackowl.tools.io.path_guard`).
- Produces: no new public interface — `ApplyPatchTool`'s existing text `.output` gains an appended `git diff` block (after the existing self-computed `difflib` block) when the workspace is a git repo.

- [ ] **Step 1: Write the failing test**

Add to `tests/tools/io/test_apply_patch.py` (the file already defines `home`/`ws` fixtures and a `_patch()` helper):

```python
import subprocess


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


class TestGitDiffAppend:
    async def test_git_repo_workspace_appends_real_diff(self, home: Path, ws: Path) -> None:
        _init_repo(ws)
        (ws / "code.py").write_text("def foo():\n    return 1\n")
        subprocess.run(["git", "add", "code.py"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add code.py"], cwd=ws, check=True)
        patch = _patch(
            "*** Update File: code.py",
            "@@ def foo():",
            "-    return 1",
            "+    return 2",
        )
        result = await ApplyPatchTool().execute(patch=patch)
        assert result.success is True
        assert "Patch applied to" in result.output  # original self-computed block, unchanged
        assert '"files_changed"' in result.output  # appended real git-diff JSON block
        assert '"code.py"' in result.output

    async def test_non_git_workspace_output_unchanged(self, home: Path, ws: Path) -> None:
        (ws / "code.py").write_text("def foo():\n    return 1\n")
        patch = _patch(
            "*** Update File: code.py",
            "@@ def foo():",
            "-    return 1",
            "+    return 2",
        )
        result = await ApplyPatchTool().execute(patch=patch)
        assert result.success is True
        assert "Patch applied to" in result.output
        assert '"files_changed"' not in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tools/io/test_apply_patch.py -k TestGitDiffAppend -v`
Expected: `test_git_repo_workspace_appends_real_diff` FAILS (no `"files_changed"` in output); `test_non_git_workspace_output_unchanged` already PASSES (locks in the baseline).

- [ ] **Step 3: Implement**

In `src/stackowl/tools/io/apply_patch.py`, change the import block (currently):

```python
from stackowl.tools.io.path_guard import is_within_root as _guard
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve
from stackowl.tools.io.undo_store import UndoStore
```

to:

```python
from stackowl.tools.io.path_guard import data_root
from stackowl.tools.io.path_guard import is_within_root as _guard
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve
from stackowl.tools.io.undo_store import UndoStore
from stackowl.tools.system.git_tool import diff_summary, is_git_repo
```

Replace the existing EXIT return (currently):

```python
        body = "\n".join(state.summary)
        diff = "\n".join(d for d in state.diffs if d)
        payload = f"Patch applied to {len(state.summary)} file(s).\n{undo_hint}\n\n{body}"
        if diff:
            payload += f"\n\n{diff}"
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)
```

with:

```python
        body = "\n".join(state.summary)
        diff = "\n".join(d for d in state.diffs if d)
        payload = f"Patch applied to {len(state.summary)} file(s).\n{undo_hint}\n\n{body}"
        if diff:
            payload += f"\n\n{diff}"
        # Independent confirmation of what changed, alongside (never replacing)
        # the self-computed diff above — best-effort: any failure is logged
        # and omitted, never fails this already-successful patch application
        # (research artifact §3 proposal 4).
        repo_dir = str(data_root())
        if await is_git_repo(repo_dir):
            git_diff = await diff_summary(repo_dir)
            if git_diff.success:
                payload += f"\n\n--- git diff (independent check) ---\n{git_diff.output}"
            else:
                log.tool.debug(
                    "apply_patch.execute: diff_summary failed — omitting git diff",
                    extra={"_fields": {"error": git_diff.error}},
                )
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tools/io/test_apply_patch.py -v`
Expected: all pass.

- [ ] **Step 5: Gate**

Run: `uv run ruff check src/stackowl/tools/io/apply_patch.py && uv run mypy src/stackowl/tools/io/apply_patch.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/tools/io/apply_patch.py tests/tools/io/test_apply_patch.py
git commit -m "feat(tools): apply_patch appends a real git diff alongside its self-computed one"
```

---

### Task 4: `edit` — append real diff after the self-computed one

**Files:**
- Modify: `src/stackowl/tools/io/edit.py`
- Test: `tests/tools/io/test_edit.py`

**Interfaces:**
- Consumes: `diff_summary(repo: str) -> ToolResult`, `is_git_repo(path: str) -> bool` (Task 1), `data_root() -> Path` (already available via `stackowl.tools.io.path_guard`).
- Produces: no new public interface — `EditTool`'s existing text `.output` gains an appended `git diff` block (after the existing self-computed `difflib` block) when the workspace is a git repo.

- [ ] **Step 1: Write the failing test**

Add to `tests/tools/io/test_edit.py` (the file already defines a `workspace` fixture):

```python
import subprocess


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


class TestGitDiffAppend:
    async def test_git_repo_workspace_appends_real_diff(self, workspace: Path) -> None:
        _init_repo(workspace)
        f = workspace / "code.py"
        f.write_text("def foo():\n    return 1\n")
        subprocess.run(["git", "add", "code.py"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add code.py"], cwd=workspace, check=True)

        result = await EditTool().execute(path=str(f), old_string="return 1", new_string="return 2")

        assert result.success is True
        assert "Undo token:" in result.output  # original self-computed block, unchanged
        assert '"files_changed"' in result.output  # appended real git-diff JSON block
        assert '"code.py"' in result.output

    async def test_non_git_workspace_output_unchanged(self, workspace: Path) -> None:
        f = workspace / "code.py"
        f.write_text("def foo():\n    return 1\n")

        result = await EditTool().execute(path=str(f), old_string="return 1", new_string="return 2")

        assert result.success is True
        assert "Undo token:" in result.output
        assert '"files_changed"' not in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tools/io/test_edit.py -k TestGitDiffAppend -v`
Expected: `test_git_repo_workspace_appends_real_diff` FAILS (no `"files_changed"` in output); `test_non_git_workspace_output_unchanged` already PASSES (locks in the baseline).

- [ ] **Step 3: Implement**

In `src/stackowl/tools/io/edit.py`, change the import block (currently):

```python
from stackowl.tools.io.path_guard import is_within_root as _guard
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve
from stackowl.tools.io.undo_store import UndoStore
```

to:

```python
from stackowl.tools.io.path_guard import data_root
from stackowl.tools.io.path_guard import is_within_root as _guard
from stackowl.tools.io.path_guard import resolve_in_workspace as _resolve
from stackowl.tools.io.undo_store import UndoStore
from stackowl.tools.system.git_tool import diff_summary, is_git_repo
```

Replace the existing EXIT block (currently lines 195-214):

```python
        # 4. EXIT — unified diff + undo token. Surface low-confidence fuzzy hits so
        # the model/user can catch a wrong-but-similar-line edit (the diff shows it).
        diff = self._unified_diff(content, new_content, path_str)
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.debug(
            "edit.execute: exit",
            extra={"_fields": {"path": path_str, "strategy": strategy, "token": token, "duration_ms": duration_ms}},
        )
        caution = ""
        if strategy in _LOW_CONFIDENCE_STRATEGIES:
            caution = (
                f"\n⚠ Matched via fuzzy strategy '{strategy}' (NOT an exact match). Confirm the diff below "
                "hit the intended line; if it edited the wrong one, call undo_write.\n"
            )
        payload = (
            f"Edited {path_str} (matched via {strategy}).\n"
            f"Undo token: {token}\n{caution}\n"
            f"{diff}"
        )
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)
```

with:

```python
        # 4. EXIT — unified diff + undo token. Surface low-confidence fuzzy hits so
        # the model/user can catch a wrong-but-similar-line edit (the diff shows it).
        diff = self._unified_diff(content, new_content, path_str)
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.debug(
            "edit.execute: exit",
            extra={"_fields": {"path": path_str, "strategy": strategy, "token": token, "duration_ms": duration_ms}},
        )
        caution = ""
        if strategy in _LOW_CONFIDENCE_STRATEGIES:
            caution = (
                f"\n⚠ Matched via fuzzy strategy '{strategy}' (NOT an exact match). Confirm the diff below "
                "hit the intended line; if it edited the wrong one, call undo_write.\n"
            )
        payload = (
            f"Edited {path_str} (matched via {strategy}).\n"
            f"Undo token: {token}\n{caution}\n"
            f"{diff}"
        )
        # Independent confirmation of what changed, alongside (never replacing)
        # the self-computed diff above — best-effort: any failure is logged
        # and omitted, never fails this already-successful edit (research
        # artifact §3 proposal 4).
        repo_dir = str(data_root())
        if await is_git_repo(repo_dir):
            git_diff = await diff_summary(repo_dir)
            if git_diff.success:
                payload += f"\n\n--- git diff (independent check) ---\n{git_diff.output}"
            else:
                log.tool.debug(
                    "edit.execute: diff_summary failed — omitting git diff",
                    extra={"_fields": {"error": git_diff.error}},
                )
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tools/io/test_edit.py -v`
Expected: all pass.

- [ ] **Step 5: Gate**

Run: `uv run ruff check src/stackowl/tools/io/edit.py && uv run mypy src/stackowl/tools/io/edit.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/tools/io/edit.py tests/tools/io/test_edit.py
git commit -m "feat(tools): edit appends a real git diff alongside its self-computed one"
```

---

### Task 5: Full regression + final gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full regression surface**

Run each individually (never the whole `tests/` tree — hangs on this sandbox per project convention):

```bash
uv run pytest tests/tools/system/test_git_tool.py -v
uv run pytest tests/tools/system/test_claude_code.py -v
uv run pytest tests/tools/io/test_apply_patch.py -v
uv run pytest tests/tools/io/test_edit.py -v
```

Expected: all pass.

- [ ] **Step 2: Final gate**

Run: `uv run ruff check src/stackowl/tools/system/git_tool.py src/stackowl/tools/system/claude_code.py src/stackowl/tools/io/apply_patch.py src/stackowl/tools/io/edit.py && uv run mypy src/stackowl/tools/system/git_tool.py src/stackowl/tools/system/claude_code.py src/stackowl/tools/io/apply_patch.py src/stackowl/tools/io/edit.py`
Expected: both clean.
