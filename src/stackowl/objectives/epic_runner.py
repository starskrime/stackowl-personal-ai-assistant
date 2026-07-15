"""Per-story background sequence for epic execution (Task #4/7).

`run_story` is what `driver.py` launches as a background `asyncio.Task` for
each ready story: create a worktree off the integration branch's current
tip, run `claude_code`, commit + test its changes, and on success merge
inline under a repo-keyed lock (re-testing the MERGED integration branch,
not just the story's own worktree). Mutates the subgoal row via `store` as
its only observable effect — no return value, matching the fire-and-forget
shape the driver launches it under.

IMPORTANT — ClaudeCodeTool double-isolation (confirmed against the real
implementation, not assumed): `stackowl.tools.system.claude_code.ClaudeCodeTool`
isolates ANY fresh (non-resume) call into its OWN scratch worktree whenever
`workdir` is itself a git repo — and a linked worktree (which is what this
module creates for a story) satisfies that check too (`git rev-parse
--is-inside-work-tree` is true inside a linked worktree; verified both by
direct experiment and by
`tests/tools/system/test_claude_code.py::test_isolates_into_worktree_when_workdir_is_git_repo`,
which asserts the edit lands in the nested worktree, not the original
`workdir`). So the story worktree this module creates (`worktree_path`
below) is only ever a base-commit PIN passed to claude_code — Claude's real
edits land in a FURTHER-nested worktree/branch reported back in the call
result's `isolation` field, which `_resolve_work_location` reads back and
every subsequent step (commit / test / merge) operates on instead. Nothing
here weakens `permission_mode="bypassPermissions"` or any other consent-
relevant call shape — this is purely about which directory holds the real
diff.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.objectives.model import Objective, Subgoal
from stackowl.objectives.store import ObjectiveStore
from stackowl.paths import StackowlHome
from stackowl.tools.system.claude_code import ClaudeCodeTool
from stackowl.tools.system.git_tool import GitTool, add_worktree
from stackowl.tools.system.run_tests import RunTestsTool
from stackowl.tools.system.shell import run_argv

__all__ = ["detect_orphan_and_recover", "run_story"]

#: Mirrors driver.py's _MAX_SUBGOAL_ATTEMPTS (=3) — kept in sync manually
#: (both constants are small and stable; a shared import would create a
#: circular dependency between driver.py and epic_runner.py).
_MAX_SUBGOAL_ATTEMPTS = 3

_TEST_COMMAND = "uv run pytest -q"


def _merge_lock(locks: dict[str, asyncio.Lock], repo: str) -> asyncio.Lock:
    lock = locks.get(repo)
    if lock is None:
        lock = asyncio.Lock()
        locks[repo] = lock
    return lock


def _resolve_work_location(
    raw_output: str, fallback_path: str, fallback_branch: str,
) -> tuple[str, str]:
    """Read claude_code's OWN nested-isolation info back out of its result.

    Claude's real edits do not land in `fallback_path` (see module docstring)
    unless isolation didn't fire (non-git workdir, or the isolation attempt
    itself failed and claude_code self-healed by falling back to running
    directly in `fallback_path`) — both cases are ``isolation.isolated ==
    False``, so falling back to `(fallback_path, fallback_branch)` is correct
    there too. Never raises: unparseable output degrades to the fallback.
    """
    try:
        parsed = json.loads(raw_output) if raw_output else {}
    except (json.JSONDecodeError, ValueError) as exc:
        log.scheduler.warning(
            "[objectives] epic_runner._resolve_work_location: claude_code output "
            "was not valid JSON — using the pre-created story worktree",
            exc_info=exc,
        )
        return fallback_path, fallback_branch
    isolation = parsed.get("isolation") if isinstance(parsed, dict) else None
    if isinstance(isolation, dict) and isolation.get("isolated") and isolation.get("worktree_path"):
        branch = isolation.get("branch")
        return str(isolation["worktree_path"]), str(branch) if branch else fallback_branch
    return fallback_path, fallback_branch


async def _cleanup_worktree(git: GitTool, repo: str, path: str, branch: str) -> None:
    """Best-effort worktree+branch cleanup before a RETRY (never before an
    escalate, which deliberately preserves the worktree for inspection).
    Failures are logged but non-fatal: a failed cleanup here only means
    slightly more disk/branch clutter, never a collision on the next
    attempt — the deterministic story worktree/branch get a second,
    unconditional pre-clean at the top of the next `run_story` call."""
    remove_result = await git(operation="worktree_remove", repo=repo, path=path, force=True)
    if not remove_result.success:
        log.scheduler.debug(
            "[objectives] epic_runner._cleanup_worktree: worktree_remove no-op/failed (best-effort)",
            extra={"_fields": {"path": path, "error": remove_result.error}},
        )
    await git(operation="branch", repo=repo, name=branch, delete=True, force=True)


async def run_story(
    objective: Objective, subgoal: Subgoal, store: ObjectiveStore, locks: dict[str, asyncio.Lock],
) -> None:
    """Execute the per-story background sequence (§Execution model, design spec)."""
    assert objective.repo is not None and objective.integration_branch is not None
    repo = objective.repo
    log.scheduler.info(
        "[objectives] epic_runner.run_story: entry",
        extra={"_fields": {"objective_id": objective.objective_id, "subgoal_id": subgoal.subgoal_id}},
    )

    # Step 1 — re-validate repo (TOCTOU) then create the worktree.
    git = GitTool()
    status_check = await git(operation="status", repo=repo)
    if not status_check.success:
        await _escalate(
            store, subgoal, f"repo is no longer a valid git repository: {status_check.error}", worktree=None,
        )
        return

    branch = f"stackowl/story-{subgoal.subgoal_id}"
    worktree_path = str(StackowlHome.worktrees_dir() / branch.replace("/", "-"))

    # Defensive pre-clean (retry-worktree-collision guard): `branch` and
    # `worktree_path` are DETERMINISTIC (derived only from subgoal_id), so a
    # prior failed attempt at this SAME subgoal may have left them behind —
    # `add_worktree` below would then fail (path/branch already exists).
    # Best-effort: nothing to remove on a first attempt, so failure here is
    # the expected common case and is not surfaced.
    await git(operation="worktree_remove", repo=repo, path=worktree_path, force=True)
    await git(operation="branch", repo=repo, name=branch, delete=True, force=True)

    add_result = await add_worktree(
        repo, worktree_path, new_branch=branch, base_ref=objective.integration_branch,
    )
    if not add_result.success:
        await _escalate(store, subgoal, f"worktree creation failed: {add_result.error}", worktree=None)
        return
    await store.update_subgoal(
        subgoal.subgoal_id, "running", worktree_path=worktree_path, story_branch=branch,
    )
    log.scheduler.debug(
        "[objectives] epic_runner.run_story: worktree created",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "worktree_path": worktree_path, "branch": branch}},
    )

    # Step 2 — claude_code. `worktree_path` here only PINS the base commit for
    # claude_code's own nested isolation (see module docstring) — the real
    # edits land wherever `_resolve_work_location` finds them below.
    claude_result = await ClaudeCodeTool()(
        prompt=subgoal.description, workdir=worktree_path, permission_mode="bypassPermissions",
    )
    if not claude_result.success:
        await _retry_or_block(store, subgoal, f"claude_code failed: {claude_result.error}")
        return

    work_path, work_branch = _resolve_work_location(claude_result.output, worktree_path, branch)
    if work_path != worktree_path:
        # claude_code isolated further — the pre-created story worktree never
        # received any edits; release it now and point the subgoal row at
        # where the real work actually is (so a crash from here on is
        # recovered against the RIGHT location — see detect_orphan_and_recover).
        await _cleanup_worktree(git, repo, worktree_path, branch)
        await store.update_subgoal(
            subgoal.subgoal_id, "running", worktree_path=work_path, story_branch=work_branch,
        )
        log.scheduler.debug(
            "[objectives] epic_runner.run_story: claude_code isolated further — switched to its worktree",
            extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "worktree_path": work_path, "branch": work_branch}},
        )

    # Step 2b — commit whatever claude_code changed. A merge only ever sees
    # COMMITS; claude_code's own worktree isolation never commits on the
    # caller's behalf (its docstring: edits are "left in place for review ...
    # NEVER auto-merged" — nothing commits them either), so epic_runner must
    # commit the story's worktree itself before it is mergeable.
    work_status = await git(operation="status", repo=work_path)
    if not work_status.success:
        await _escalate(
            store, subgoal, f"could not read story worktree status: {work_status.error}", worktree=work_path,
        )
        return
    work_status_record = json.loads(work_status.output) if work_status.output else {}
    if work_status_record.get("clean", True):
        if work_path != worktree_path:
            await _cleanup_worktree(git, repo, work_path, work_branch)
        await _retry_or_block(store, subgoal, "claude_code made no changes in the story worktree")
        return
    commit_result = await git(
        operation="commit", repo=work_path, add_all=True,
        message=f"story {subgoal.subgoal_id}: {subgoal.description}"[:200],
    )
    if not commit_result.success:
        if work_path != worktree_path:
            await _cleanup_worktree(git, repo, work_path, work_branch)
        await _retry_or_block(store, subgoal, f"could not commit story changes: {commit_result.error}")
        return

    # Step 3 — run_tests (story's own worktree).
    story_tests = await RunTestsTool()(command=_TEST_COMMAND, workdir=work_path)
    story_record = json.loads(story_tests.output) if story_tests.success and story_tests.output else {}
    if not story_tests.success or not story_record.get("all_passed"):
        if work_path != worktree_path:
            await _cleanup_worktree(git, repo, work_path, work_branch)
        await _retry_or_block(
            store, subgoal, f"tests failed in story worktree: {story_record or story_tests.error}",
        )
        return

    # Step 4 — merge under a repo-keyed lock.
    lock = _merge_lock(locks, repo)
    async with lock:
        merge = await _merge_branch(repo, work_branch, objective.integration_branch)
        if merge == "conflict":
            log.scheduler.warning(
                "[objectives] epic_runner.run_story: merge conflict — escalating",
                extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
            )
            await _escalate(store, subgoal, "merge conflict with integration branch", worktree=work_path)
            return
        if merge == "failed":
            await _escalate(store, subgoal, "merge into integration branch failed", worktree=work_path)
            return

        # Re-test the MERGED integration branch, not just the story's worktree.
        integration_tests = await RunTestsTool()(command=_TEST_COMMAND, workdir=repo)
        integration_record = (
            json.loads(integration_tests.output) if integration_tests.success and integration_tests.output else {}
        )
        if not integration_tests.success or not integration_record.get("all_passed"):
            await _escalate(
                store, subgoal,
                f"merge succeeded but integration tests failed: {integration_record or integration_tests.error}",
                worktree=work_path,
            )
            return

    # Clean merge + integration tests pass — clean up the worktree, keep the
    # branch. force=True: running tests (Step 3) left behind untracked build
    # artifacts (e.g. .pytest_cache/), and `git worktree remove` refuses a
    # worktree with ANY untracked files without --force (confirmed against
    # real git — not just uncommitted TRACKED changes, which the commit step
    # already cleared). A cleanup failure here does not change the subgoal's
    # already-verified "done" outcome, but must never be silent.
    cleanup_result = await git(operation="worktree_remove", repo=repo, path=work_path, force=True)
    if not cleanup_result.success:
        log.scheduler.warning(
            "[objectives] epic_runner.run_story: worktree cleanup failed after a verified done — leaving it in place",
            extra={"_fields": {
                "subgoal_id": subgoal.subgoal_id, "worktree_path": work_path, "error": cleanup_result.error,
            }},
        )
    await store.update_subgoal(subgoal.subgoal_id, "done", result="merged and verified", worktree_path=None)
    await store.append_event(objective.objective_id, "subgoal_done", subgoal.description)
    log.scheduler.info(
        "[objectives] epic_runner.run_story: exit — done",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
    )


async def _merge_branch(repo: str, branch: str, integration_branch: str) -> str:
    """Merge `branch` into `integration_branch` via a direct git CLI call
    (GitTool has no dedicated "merge" operation — this is the one place that
    needs it, kept local rather than growing GitTool's surface for a single
    caller). Returns "ok" | "conflict" | "failed"."""
    checkout = await run_argv(
        ["git", "checkout", integration_branch], tool_name="git", workdir=repo, intent="write",
    )
    if not checkout.success:
        return "failed"
    merge = await run_argv(
        ["git", "merge", "--no-ff", branch], tool_name="git", workdir=repo, intent="write",
    )
    if merge.success:
        return "ok"
    await run_argv(["git", "merge", "--abort"], tool_name="git", workdir=repo, intent="write")
    return "conflict" if "conflict" in (merge.error or "").lower() else "failed"


async def _retry_or_block(store: ObjectiveStore, subgoal: Subgoal, reason: str) -> None:
    used = subgoal.attempts + 1
    if used < _MAX_SUBGOAL_ATTEMPTS:
        await store.update_subgoal(subgoal.subgoal_id, "pending", result=reason, attempts=used)
        log.scheduler.info(
            "[objectives] epic_runner: story failed — retrying",
            extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "attempt": used}},
        )
        return
    await store.update_subgoal(subgoal.subgoal_id, "blocked", result=reason, attempts=used)
    log.scheduler.warning(
        "[objectives] epic_runner: story failed — retry budget exhausted, blocked",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "reason": reason}},
    )


async def _escalate(store: ObjectiveStore, subgoal: Subgoal, reason: str, *, worktree: str | None) -> None:
    """Escalate a story to blocked WITHOUT consuming the retry budget — used
    for outcomes a clean retry would not fix (merge conflict, integration
    test failure): the work is preserved (worktree left for inspection)."""
    await store.update_subgoal(subgoal.subgoal_id, "blocked", result=reason)
    log.scheduler.warning(
        "[objectives] epic_runner: story escalated",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "reason": reason, "worktree": worktree}},
    )


async def detect_orphan_and_recover(
    objective: Objective, subgoal: Subgoal, store: ObjectiveStore,
) -> None:
    """Crash recovery — worktree-aware orphan handling (§Execution model).
    Caller (driver.py) already confirmed this subgoal is `running` and NOT in
    the process's local live-task set before calling this."""
    assert objective.repo is not None
    if subgoal.worktree_path is None or not Path(subgoal.worktree_path).exists():
        # Never got far enough to create a worktree, or it's already gone —
        # safe to just restart.
        await store.update_subgoal(subgoal.subgoal_id, "pending", attempts=subgoal.attempts + 1)
        return
    git = GitTool()
    status = await git(operation="status", repo=subgoal.worktree_path)
    if not status.success:
        await _escalate(
            store, subgoal, "orphan recovery: could not read worktree git status",
            worktree=subgoal.worktree_path,
        )
        return
    record = json.loads(status.output) if status.output else {}
    if record.get("clean", False):
        await git(
            operation="worktree_remove", repo=objective.repo,
            path=subgoal.worktree_path, force=True,
        )
        await store.update_subgoal(
            subgoal.subgoal_id, "pending", attempts=subgoal.attempts + 1, worktree_path=None,
        )
        log.scheduler.info(
            "[objectives] epic_runner.detect_orphan_and_recover: clean tree — restarting fresh",
            extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
        )
        return
    await _escalate(
        store, subgoal, f"orphan recovery: worktree is dirty — {record}",
        worktree=subgoal.worktree_path,
    )
