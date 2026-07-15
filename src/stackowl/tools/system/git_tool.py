"""GitTool — structured git operations (status/diff/commit/branch/worktree).

Reuses the shared subprocess seam (:func:`stackowl.tools.system.shell.run_argv`)
for the actual spawn/timeout/logging instead of re-implementing it — this tool
only builds argv per operation and parses git's own plumbing-friendly output
(``--porcelain``, ``--numstat``, ``--format``) into structured dicts, never raw
text dumps. Exec mode (no shell) throughout: git receives argv directly, so a
commit message or branch name can never be shell-interpreted.

Six operations: ``status``, ``diff``, ``commit``, ``branch``, ``worktree_add``,
``worktree_remove``. Not a full git CLI wrapper — only what epic orchestration
(Task #4) needs: read the repo state, inspect a change, commit it, and manage
worktrees for isolated story execution.
"""

from __future__ import annotations

import json
import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.system.shell import _default_workspace_cwd, run_argv

__all__ = ["GitTool", "add_worktree", "diff_summary", "is_git_repo"]

_TOOLSET_GROUP = "code"
_OPERATIONS = frozenset(
    {"status", "diff", "commit", "branch", "worktree_add", "worktree_remove"}
)
_DEFAULT_MAX_DIFF_CHARS = 6000


def _resolve_repo(raw: object) -> str:
    """Resolve the target repo dir — an explicit ``repo`` wins, else the workspace."""
    if isinstance(raw, str) and raw.strip():
        return raw
    return _default_workspace_cwd() or "."


def _parse_status(output: str) -> dict[str, object]:
    """Parse ``git status --porcelain=v1 -b`` into a structured dict.

    Line 1 (when present) is ``## <branch>...<upstream> [ahead N, behind M]``;
    every following line is ``XY path`` (a rename line embeds `` -> `` — the
    part after it is the new path). Never raises on a malformed line — it is
    skipped rather than aborting the whole parse.
    """
    lines = output.splitlines()
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    files: list[dict[str, object]] = []
    for line in lines:
        if line.startswith("## "):
            header = line[3:]
            branch_part, _, ab_part = header.partition(" [")
            branch, _, upstream = branch_part.partition("...")
            branch = branch or None
            upstream = upstream or None
            if branch == "HEAD (no branch)":
                branch = None
            if ab_part:
                ab_part = ab_part.rstrip("]")
                for token in ab_part.split(", "):
                    if token.startswith("ahead "):
                        ahead = int(token[len("ahead ") :] or 0)
                    elif token.startswith("behind "):
                        behind = int(token[len("behind ") :] or 0)
            continue
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(
            {
                "path": path,
                "index_status": xy[0],
                "worktree_status": xy[1],
                "untracked": xy == "??",
            }
        )
    return {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "files": files,
        "clean": not files,
    }


def _parse_numstat(output: str) -> tuple[int, int, list[dict[str, object]]]:
    """Parse ``git diff/show --numstat`` lines into (insertions, deletions, files)."""
    insertions = 0
    deletions = 0
    files: list[dict[str, object]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        ins_raw, del_raw, path = parts
        ins = int(ins_raw) if ins_raw.isdigit() else 0
        dels = int(del_raw) if del_raw.isdigit() else 0
        insertions += ins
        deletions += dels
        files.append({"path": path, "insertions": ins, "deletions": dels, "binary": ins_raw == "-"})
    return insertions, deletions, files


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[truncated, showing {max_chars} of {len(text)} chars]"


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


async def is_git_repo(path: str) -> bool:
    """True if ``path`` is inside a git working tree. Never raises — any failure
    (missing dir, not a repo, git not installed) resolves to False.

    Module-level (not a GitTool method) so a caller that needs a cheap git-repo
    check without the full Tool wrapper — e.g. claude_code's worktree-isolation
    decision — can call it directly.
    """
    result = await run_argv(
        ["git", "rev-parse", "--is-inside-work-tree"], tool_name="git", workdir=path, intent="read",
    )
    return result.success and result.output.strip() == "true"


async def add_worktree(
    repo: str, path: str, *, new_branch: str | None = None, base_ref: str | None = None,
) -> ToolResult:
    """Create a git worktree at ``path`` off ``repo``. The shared plumbing behind
    both ``GitTool``'s ``worktree_add`` operation and claude_code's worktree
    isolation — one argv-building implementation, not two."""
    argv = ["git", "worktree", "add"]
    if new_branch:
        argv += ["-b", new_branch]
    argv.append(path)
    if base_ref:
        argv.append(base_ref)
    return await run_argv(argv, tool_name="git", workdir=repo, intent="write")


class GitTool(Tool):
    """Structured git status/diff/commit/branch/worktree operations."""

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return (
            "Run a structured git operation against a repo — never a raw text "
            "dump. Operations: 'status' (branch/ahead/behind + per-file status "
            "codes), 'diff' (files changed + insertion/deletion counts + a "
            "bounded unified diff), 'commit' (stage + commit, returns the new "
            "sha + stats), 'branch' (list, create, or delete a branch), "
            "'worktree_add' (create an isolated worktree checkout), "
            "'worktree_remove' (remove one). Args: 'operation' (required); "
            "'repo' working directory (defaults to the StackOwl workspace)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": sorted(_OPERATIONS),
                    "description": "Which git operation to run.",
                },
                "repo": {
                    "type": "string",
                    "description": (
                        "Repo directory to run in (defaults to the StackOwl "
                        "workspace)."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "commit: the commit message (required for commit).",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "commit: specific paths to stage before committing. "
                        "diff: limit the diff to these paths. Omitted for "
                        "commit ⇒ nothing is staged (git add -A is never run "
                        "implicitly — set add_all=true for that)."
                    ),
                },
                "add_all": {
                    "type": "boolean",
                    "description": "commit: stage all changes (git add -A) before committing.",
                },
                "staged": {
                    "type": "boolean",
                    "description": "diff: diff the index (git diff --cached) instead of the worktree.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"diff: max chars of raw diff text to return (default {_DEFAULT_MAX_DIFF_CHARS}).",
                },
                "name": {
                    "type": "string",
                    "description": "branch: the branch name to create, delete, or list under.",
                },
                "base_ref": {
                    "type": "string",
                    "description": (
                        "branch: base commit-ish for a new branch (default HEAD). "
                        "worktree_add: commit-ish to check out (default HEAD)."
                    ),
                },
                "checkout": {
                    "type": "boolean",
                    "description": "branch: switch to the newly created branch (default false).",
                },
                "delete": {
                    "type": "boolean",
                    "description": "branch: delete 'name' instead of creating/listing.",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "branch: force-delete an unmerged branch (-D). "
                        "worktree_remove: force-remove a dirty worktree."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "worktree_add/worktree_remove: the worktree directory.",
                },
                "new_branch": {
                    "type": "string",
                    "description": "worktree_add: create this new branch (-b) for the worktree.",
                },
            },
            "required": ["operation"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="unconfirmed",
            toolset_group=_TOOLSET_GROUP,
            progress_key="RUN_CMD",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        operation = str(kwargs.get("operation", "")).strip()
        repo = _resolve_repo(kwargs.get("repo"))
        # 1. ENTRY
        log.tool.debug(
            "git.execute: entry",
            extra={"_fields": {"operation": operation, "repo": repo}},
        )
        if operation not in _OPERATIONS:
            return self._err(f"unknown operation {operation!r}", t0, committed=False)

        # 2. DECISION — dispatch by operation; read ops declare intent='read' so a
        # failed status/diff never trips the effectful-failure floor.
        try:
            if operation == "status":
                return await self._status(repo, t0)
            if operation == "diff":
                return await self._diff(repo, kwargs, t0)
            if operation == "commit":
                return await self._commit(repo, kwargs, t0)
            if operation == "branch":
                return await self._branch(repo, kwargs, t0)
            if operation == "worktree_add":
                return await self._worktree_add(repo, kwargs, t0)
            return await self._worktree_remove(repo, kwargs, t0)
        except Exception as exc:  # no-hidden-errors — dispatch must never raise
            log.tool.error(
                "git.execute: operation raised",
                exc_info=exc,
                extra={"_fields": {"operation": operation}},
            )
            return self._err(f"{operation} failed: {exc}", t0)

    async def _status(self, repo: str, t0: float) -> ToolResult:
        result = await run_argv(
            ["git", "status", "--porcelain=v1", "-b"],
            tool_name="git", workdir=repo, intent="read",
        )
        if not result.success:
            return result
        parsed = _parse_status(result.output)
        files = parsed["files"]
        file_count = len(files) if isinstance(files, list) else 0
        log.tool.debug(
            "git.status: exit",
            extra={"_fields": {"files": file_count, "clean": parsed["clean"]}},
        )
        return self._ok(parsed, t0, result.duration_ms, committed=False)

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

    async def _commit(self, repo: str, kwargs: dict[str, object], t0: float) -> ToolResult:
        message = str(kwargs.get("message", "")).strip()
        if not message:
            return self._err("commit requires a non-empty 'message'", t0, committed=False)
        paths = kwargs.get("paths")
        path_args = [str(p) for p in paths] if isinstance(paths, list) else []
        add_all = bool(kwargs.get("add_all", False))
        duration_ms = 0.0

        if path_args:
            add_result = await run_argv(
                ["git", "add", "--"] + path_args, tool_name="git", workdir=repo, intent="write",
            )
            duration_ms += add_result.duration_ms
            if not add_result.success:
                return add_result
        elif add_all:
            add_result = await run_argv(
                ["git", "add", "-A"], tool_name="git", workdir=repo, intent="write",
            )
            duration_ms += add_result.duration_ms
            if not add_result.success:
                return add_result

        commit_result = await run_argv(
            ["git", "commit", "-m", message], tool_name="git", workdir=repo, intent="write",
        )
        duration_ms += commit_result.duration_ms
        if not commit_result.success:
            return commit_result

        show_result = await run_argv(
            ["git", "show", "-s", "--format=%H%x09%s", "HEAD"],
            tool_name="git", workdir=repo, intent="read",
        )
        duration_ms += show_result.duration_ms
        if show_result.success:
            sha, _, summary = show_result.output.partition("\t")
        else:
            # The commit itself landed (checked above) — only this READ-BACK of its
            # sha/summary failed. Degrade gracefully (never fail a real commit on a
            # follow-up read), but log loudly: silently returning "" would look like
            # a normal empty field instead of a lost read.
            sha, summary = "", ""
            log.tool.warning(
                "git.commit: post-commit 'git show' failed — sha/summary unavailable",
                extra={"_fields": {"error": (show_result.error or "")[:200]}},
            )

        numstat_result = await run_argv(
            ["git", "show", "--numstat", "--format=", "HEAD"],
            tool_name="git", workdir=repo, intent="read",
        )
        duration_ms += numstat_result.duration_ms
        if numstat_result.success:
            insertions, deletions, files = _parse_numstat(numstat_result.output)
        else:
            insertions, deletions, files = 0, 0, []
            log.tool.warning(
                "git.commit: post-commit 'git show --numstat' failed — stats unavailable",
                extra={"_fields": {"error": (numstat_result.error or "")[:200]}},
            )

        parsed = {
            "sha": sha,
            "short_sha": sha[:12] if sha else "",
            "summary": summary or message,
            "files_changed": len(files),
            "insertions": insertions,
            "deletions": deletions,
        }
        log.tool.debug("git.commit: exit", extra={"_fields": {"sha": parsed["short_sha"]}})
        return self._ok(parsed, t0, duration_ms, committed=True)

    async def _branch(self, repo: str, kwargs: dict[str, object], t0: float) -> ToolResult:
        name = str(kwargs.get("name", "")).strip() or None
        delete = bool(kwargs.get("delete", False))
        force = bool(kwargs.get("force", False))
        checkout = bool(kwargs.get("checkout", False))
        base_ref = str(kwargs.get("base_ref", "")).strip() or None

        if delete:
            if not name:
                return self._err("branch delete requires 'name'", t0, committed=False)
            result = await run_argv(
                ["git", "branch", "-D" if force else "-d", name],
                tool_name="git", workdir=repo, intent="write",
            )
            if not result.success:
                return result
            return self._ok({"deleted": name}, t0, result.duration_ms, committed=True)

        if name:
            if checkout:
                argv = ["git", "checkout", "-b", name] + ([base_ref] if base_ref else [])
            else:
                argv = ["git", "branch", name] + ([base_ref] if base_ref else [])
            result = await run_argv(argv, tool_name="git", workdir=repo, intent="write")
            if not result.success:
                return result
            return self._ok(
                {"created": name, "checked_out": checkout}, t0, result.duration_ms, committed=True,
            )

        # No name/delete ⇒ list branches, structured.
        result = await run_argv(
            ["git", "branch", "--format=%(refname:short)%09%(objectname:short)%09%(HEAD)"],
            tool_name="git", workdir=repo, intent="read",
        )
        if not result.success:
            return result
        branches = []
        current: str | None = None
        for line in result.output.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            bname, sha, head_mark = parts
            is_current = head_mark == "*"
            if is_current:
                current = bname
            branches.append({"name": bname, "sha": sha, "current": is_current})
        return self._ok(
            {"branches": branches, "current": current}, t0, result.duration_ms, committed=False,
        )

    async def _worktree_add(self, repo: str, kwargs: dict[str, object], t0: float) -> ToolResult:
        path = str(kwargs.get("path", "")).strip()
        if not path:
            return self._err("worktree_add requires 'path'", t0, committed=False)
        new_branch = str(kwargs.get("new_branch", "")).strip() or None
        base_ref = str(kwargs.get("base_ref", "")).strip() or None

        result = await add_worktree(repo, path, new_branch=new_branch, base_ref=base_ref)
        if not result.success:
            return result
        return self._ok(
            {"path": path, "branch": new_branch, "base_ref": base_ref},
            t0, result.duration_ms, committed=True,
        )

    async def _worktree_remove(self, repo: str, kwargs: dict[str, object], t0: float) -> ToolResult:
        path = str(kwargs.get("path", "")).strip()
        if not path:
            return self._err("worktree_remove requires 'path'", t0, committed=False)
        force = bool(kwargs.get("force", False))

        argv = ["git", "worktree", "remove"] + (["--force"] if force else []) + [path]
        result = await run_argv(argv, tool_name="git", workdir=repo, intent="write")
        if not result.success:
            return result
        return self._ok({"path": path, "removed": True}, t0, result.duration_ms, committed=True)

    @staticmethod
    def _ok(
        parsed: dict[str, object], t0: float, duration_ms: float, *, committed: bool,
    ) -> ToolResult:
        # 4. EXIT
        total_ms = max(duration_ms, (time.monotonic() - t0) * 1000)
        log.tool.debug(
            "git.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": total_ms}},
        )
        return ToolResult(
            success=True, output=json.dumps(parsed, ensure_ascii=False),
            duration_ms=total_ms, side_effect_committed=committed,
        )

    @staticmethod
    def _err(msg: str, t0: float, *, committed: bool = True) -> ToolResult:
        msg = f"git: {msg}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "git.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms, side_effect_committed=committed,
        )
