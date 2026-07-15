# Design: real diff surfaced after every code-editing call

Date: 2026-07-14
Status: approved
Source: "Making StackOwl Excellent at Coding" research artifact, §3 Proposal 4 (P1)

## Problem

`claude_code`, `apply_patch`, and `edit` all self-report what changed:

- `claude_code` — nothing today. Its `ToolResult.output` is just the CLI's own
  JSON result text; no diff of any kind.
- `apply_patch` / `edit` — a `difflib.unified_diff` computed from the tool's
  own in-memory before/after buffers, folded as text into `.output`.

None of the three independently confirm the change actually landed on disk
against git's own view of the working tree. A self-computed in-memory diff
can't detect e.g. a write that silently failed after the buffer was built.

## Design

### 1. Shared helper — `diff_summary()` in `git_tool.py`

Extract the body of `GitTool._diff` into a new module-level free function:

```python
async def diff_summary(repo: str, *, max_chars: int = _DEFAULT_MAX_DIFF_CHARS) -> str | None:
```

Placed alongside the existing `is_git_repo`/`add_worktree` module-level
helpers (`git_tool.py`) — same rationale already documented there: a caller
needing git info without the full `Tool.__call__` wrapper (consent gate,
4-point logging span, `TestModeGuard` assertion) calls the free function
directly. Returns the same JSON-shaped string `GitTool._diff` already
produces (`files_changed`/`insertions`/`deletions`/`files`/`diff`), or `None`
if the diff call itself fails (non-repo, git error, etc.) — never raises.

`GitTool._diff` becomes a thin wrapper: call `diff_summary`, wrap in
`ToolResult` via the existing `_ok`/error path. Zero behavior change to the
`GitTool` tool surface or its tests.

### 2. `claude_code` — pure addition

After a successful run, if `is_git_repo(workdir)` (the tool already imports
and uses this helper for worktree-isolation decisions), call
`diff_summary(workdir)` and fold the result into the existing JSON payload
under a new `"diff"` key. `workdir` here is the actual directory the run
executed in (post worktree-isolation resolution) — the correct target.

### 3. `apply_patch` / `edit` — additive append

Keep the existing self-computed `difflib` diff in `.output` completely
unchanged (never remove a working capability). When `is_git_repo(target_dir)`
is true, append a second block after it: the real `diff_summary()` output.
Two independent signals in the same payload — "what the tool believes it
changed" (self-report) and "what git independently observes changed"
(reality check) — never a replacement of one by the other.

`target_dir` for both tools is `path_guard.data_root()` (today's implicit
workspace root — neither tool takes an explicit `repo`/`workdir` arg).

### 4. Non-git targets

Unchanged. `is_git_repo()` returns `False` for anything that isn't a git
repo (module-level, never raises) — the new code path is skipped entirely
and every existing test/behavior for non-git workspaces is untouched.

### 5. Failure handling

`diff_summary()` never raises (matches `is_git_repo`'s existing contract).
Any internal failure (git binary unavailable, corrupted repo, etc.) is
logged via `log.tool.warning(...)` and returns `None` — callers treat `None`
as "omit the diff block," never as a reason to fail the parent tool call.

### 6. Size bound

Reuse the existing `_DEFAULT_MAX_DIFF_CHARS` (6000) truncation logic
already in `git_tool.py` (`_truncate`) — no new constant.

## Testing

- `git_tool.py`: existing `GitTool`/`_diff` tests must stay green unchanged
  (behavior-preserving extraction) — add one direct test of the new
  `diff_summary()` free function (git repo, non-git dir, and a
  `run_argv`-failure case returning `None`).
- `claude_code.py`: new test — successful run in a git repo asserts the
  JSON payload's `"diff"` key is populated; a run in a non-git workdir
  asserts no `"diff"` key (or `None`) and no behavior change.
- `apply_patch.py` / `edit.py`: new test per tool — a git-repo target's
  `.output` contains both the original difflib block AND the new git-diff
  block; a non-git target's `.output` is byte-identical to today.

## Out of scope

- No `ToolResult` schema change (no new field) — diff text is folded into
  existing `.output`, matching all three tools' current convention.
- No change to `write_file.py` (single-file overwrite, not in the original
  proposal's three named tools).
- No UI/consent-summary changes — this is purely a `ToolResult.output`
  enrichment, observable the same way the rest of each tool's output
  already is.
