"""ClaudeCodeTool — drive the Claude Code CLI headlessly as a subprocess.

Lets the pipeline delegate an open-ended coding task to Claude Code itself,
running non-interactively (``claude -p "<prompt>" --output-format json``) so
there is no TTY to block on. Reuses the shared subprocess/timeout/logging seam
(:func:`stackowl.tools.system.shell.run_argv`) instead of re-implementing it —
this tool only builds the argv and parses Claude Code's own JSON summary back
out.

CONSEQUENTIAL: this spawns an autonomous agent that can read/edit files and run
shell commands in ``workdir`` on the HOST (no isolated sandbox — unlike
``execute_code``); the consent gate fires before every call, and a delegated
child (delegation_depth>0) is refused (SEC-3/F163 self-defense, mirroring
``execute_code``/``process``/``sessions_spawn``).

WORKTREE ISOLATION (Task #2, coding-capability build plan): when ``workdir``
is a git repo (and this is not a ``--resume``), the run happens in a fresh
``git worktree`` on a throwaway ``stackowl/claude-code-<id>`` branch instead of
directly in ``workdir`` — Claude Code's edits land on a scratch branch, never
directly on the repo's checked-out branch. The worktree/branch are left in
place for review (git diff/log against it) and are NEVER auto-merged or
auto-removed — that is a separate, explicit step (via the ``git`` tool).
``resume_session_id`` skips isolation (falls back to direct ``workdir``): a
resumed run must see the same files its earlier turn edited, and there is no
session→worktree mapping (yet — Task #4 owns chaining worktrees across a
story's steps). A non-git ``workdir`` also skips isolation unchanged.

Requires the ``claude`` CLI on PATH. Not found → structured "unavailable",
never a silent no-op or host fallback.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.child_exclusion import child_excluded_now
from stackowl.tools.system.git_tool import add_worktree, is_git_repo
from stackowl.tools.system.shell import run_argv

__all__ = ["ClaudeCodeTool"]

_TOOLSET_GROUP = "code"

# Coding tasks run far longer than an ordinary shell command — separate, larger
# bounds from shell.py's (120s default / 300s ceiling), sized for one command.
_TIMEOUT_SEC = 600.0
_TIMEOUT_CEILING_SEC = 1800.0

_PERMISSION_MODES = frozenset({"default", "plan", "acceptEdits", "bypassPermissions"})
_DEFAULT_PERMISSION_MODE = "acceptEdits"


def _resolve_timeout(raw: object) -> float:
    """Bounded per-call timeout — mirrors shell._resolve_timeout, own bounds."""
    if raw is None:
        return _TIMEOUT_SEC
    try:
        requested = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _TIMEOUT_SEC
    if requested <= 0:
        return _TIMEOUT_SEC
    return min(requested, _TIMEOUT_CEILING_SEC)


class ClaudeCodeTool(Tool):
    """Run Claude Code headlessly on a coding task; returns its final result."""

    @property
    def name(self) -> str:
        return "claude_code"

    @property
    def description(self) -> str:
        return (
            "Delegate an open-ended coding task to Claude Code, running "
            "headlessly (non-interactive) against a working directory. Use for "
            "multi-step code changes a single shell command can't express — "
            "implement a feature, fix a bug across files, run tests and iterate. "
            "Claude Code reads/edits files and runs shell commands in workdir on "
            "this HOST (not an isolated sandbox, unlike execute_code). If workdir "
            "is a git repo, the run happens in an isolated worktree on a scratch "
            "branch (never auto-merged — review via the git tool). Args: "
            "'prompt' (required) the full task — Claude Code does not see this "
            "conversation; 'workdir' (defaults to the StackOwl workspace) the "
            "repo/directory it operates in; 'permission_mode' (default "
            "'acceptEdits') maps to the CLI's --permission-mode; "
            "'resume_session_id' to continue a prior claude_code run instead of "
            f"starting fresh; 'timeout' seconds (default {int(_TIMEOUT_SEC)}, max "
            f"{int(_TIMEOUT_CEILING_SEC)}). CONSEQUENTIAL: the user approves "
            "before every call. Not installed → returns 'unavailable'."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The coding task to give Claude Code, in full — it does "
                        "not see this conversation, only what you write here."
                    ),
                },
                "workdir": {
                    "type": "string",
                    "description": (
                        "Directory Claude Code operates in (its cwd — normally a "
                        "repo checkout). Defaults to the StackOwl workspace "
                        "directory."
                    ),
                },
                "permission_mode": {
                    "type": "string",
                    "enum": sorted(_PERMISSION_MODES),
                    "description": (
                        "Claude Code's --permission-mode. 'acceptEdits' (default) "
                        "auto-approves file edits; 'bypassPermissions' also "
                        "auto-approves shell commands — use only in a directory "
                        "you fully trust. 'plan' has it plan without changing "
                        "anything. 'default' prompts for approval and WILL HANG "
                        "here (no TTY to answer) — avoid."
                    ),
                },
                "resume_session_id": {
                    "type": "string",
                    "description": (
                        "A prior claude_code session_id (from an earlier call's "
                        "output) to continue that session instead of starting new."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        f"Per-call timeout in seconds (default {int(_TIMEOUT_SEC)}, "
                        f"bounded to {int(_TIMEOUT_CEILING_SEC)})."
                    ),
                },
            },
            "required": ["prompt"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            commit_coupling="unconfirmed",
            toolset_group=_TOOLSET_GROUP,
        )

    def consent_summary(self, **call_args: object) -> str | None:
        """Bounded, trusted digest for the consent prompt (mirrors execute_code)."""
        prompt = call_args.get("prompt")
        prompt = prompt if isinstance(prompt, str) else ""
        workdir = call_args.get("workdir")
        workdir = (
            workdir if isinstance(workdir, str) and workdir else str(StackowlHome.workspace())
        )
        mode = call_args.get("permission_mode")
        mode = mode if isinstance(mode, str) and mode in _PERMISSION_MODES else _DEFAULT_PERMISSION_MODE
        digest = prompt[:400] + ("…" if len(prompt) > 400 else "")
        return f"Run Claude Code in {workdir} (permission mode: {mode}):\n```\n{digest}\n```"

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        prompt = str(kwargs.get("prompt", "")).strip()
        # 1. ENTRY
        log.tool.debug(
            "claude_code.execute: entry",
            extra={"_fields": {"prompt_len": len(prompt), "workdir": kwargs.get("workdir")}},
        )
        if not prompt:
            return self._err("no prompt given", t0, committed=False)

        # SEC-3/F163 self-defense — a delegated sub-agent must not spawn its own
        # autonomous coding agent (mirrors execute_code/process/sessions_spawn).
        if child_excluded_now("claude_code"):
            log.tool.warning(
                "claude_code.execute: refused — child-excluded at delegation_depth>0",
                extra={"_fields": {"delegation_depth": TraceContext.get().get("delegation_depth")}},
            )
            return self._err(
                "claude_code is refused for a delegated sub-agent "
                "(delegation_depth>0); only a top-level turn may run it.",
                t0,
                committed=False,
            )

        # 2. DECISION — resolve the binary; never a silent no-op if absent.
        binary = shutil.which("claude")
        if binary is None:
            log.tool.warning("claude_code.execute: 'claude' CLI not on PATH — unavailable")
            return self._err(
                "Claude Code CLI ('claude') is not installed / not on PATH", t0, committed=False,
            )

        target_workdir = str(kwargs.get("workdir", "")) or str(StackowlHome.workspace())
        mode = str(kwargs.get("permission_mode", "")) or _DEFAULT_PERMISSION_MODE
        if mode not in _PERMISSION_MODES:
            log.tool.warning(
                "claude_code.execute: unknown permission_mode — falling back to default",
                extra={"_fields": {"requested": mode, "using": _DEFAULT_PERMISSION_MODE}},
            )
            mode = _DEFAULT_PERMISSION_MODE
        resume = str(kwargs.get("resume_session_id", "")) or None
        timeout_sec = _resolve_timeout(kwargs.get("timeout"))

        workdir, isolation = await self._resolve_workdir(target_workdir, resume=resume)

        argv = [binary, "-p", prompt, "--output-format", "json", "--permission-mode", mode]
        if resume:
            argv.extend(["--resume", resume])

        # 3. STEP — the shared subprocess/timeout/catastrophic-consent/logging seam.
        log.tool.debug(
            "claude_code.execute: launching",
            extra={
                "_fields": {
                    "workdir": workdir, "permission_mode": mode,
                    "resumed": bool(resume), "timeout_sec": timeout_sec,
                    "isolated": isolation["isolated"],
                }
            },
        )
        result = await run_argv(
            argv,
            tool_name="claude_code",
            workdir=workdir,
            timeout_sec=timeout_sec,
            shell_command=None,  # exec mode — no shell interpretation of the prompt
            intent="write",
        )
        if not result.success:
            log.tool.warning(
                "claude_code.execute: exit — process failed",
                extra={"_fields": {"error": (result.error or "")[:200], "duration_ms": result.duration_ms}},
            )
            return result

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

    @staticmethod
    async def _resolve_workdir(
        target_workdir: str, *, resume: str | None,
    ) -> tuple[str, dict[str, object]]:
        """Isolate a fresh (non-resume) git-repo run into a throwaway worktree.

        Returns ``(workdir_to_run_in, isolation_info)``. Never blocks the run: a
        non-git target, a resume, or any isolation failure all fall back to
        ``target_workdir`` unchanged (self-healing — isolation is an enhancement,
        not a precondition for running at all).
        """
        if resume:
            return target_workdir, {"isolated": False, "reason": "resume_session_id set"}
        if not await is_git_repo(target_workdir):
            return target_workdir, {"isolated": False, "reason": "workdir is not a git repo"}

        branch = f"stackowl/claude-code-{uuid.uuid4().hex[:10]}"
        worktrees_root = StackowlHome.worktrees_dir()
        try:
            worktrees_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.tool.warning(
                "claude_code._resolve_workdir: could not create worktrees_dir — skipping isolation",
                exc_info=exc,
                extra={"_fields": {"worktrees_root": str(worktrees_root)}},
            )
            return target_workdir, {"isolated": False, "reason": "worktrees_dir unavailable"}

        worktree_path = worktrees_root / branch.replace("/", "-")
        add_result = await add_worktree(target_workdir, str(worktree_path), new_branch=branch)
        if not add_result.success:
            log.tool.warning(
                "claude_code._resolve_workdir: worktree isolation failed — running directly in workdir",
                extra={"_fields": {"error": (add_result.error or "")[:200], "target_workdir": target_workdir}},
            )
            return target_workdir, {"isolated": False, "reason": "worktree_add failed"}

        log.tool.info(
            "claude_code._resolve_workdir: isolated to worktree",
            extra={"_fields": {"branch": branch, "worktree_path": str(worktree_path)}},
        )
        return str(worktree_path), {
            "isolated": True,
            "worktree_path": str(worktree_path),
            "branch": branch,
            "base_repo": target_workdir,
        }

    @staticmethod
    def _parse_output(raw: str) -> object:
        """Parse Claude Code's ``--output-format json`` summary; never raise.

        Falls back to the raw text wrapped in a dict so a change in the CLI's
        output shape degrades gracefully instead of losing the run's output.
        """
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            log.tool.warning(
                "claude_code._parse_output: non-JSON output — falling back to raw text",
                extra={"_fields": {"err": type(exc).__name__, "raw_len": len(raw)}},
            )
            return {"result": raw}

    @staticmethod
    def _err(msg: str, t0: float, *, committed: bool = True) -> ToolResult:
        msg = f"claude_code: {msg}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "claude_code.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms, side_effect_committed=committed,
        )
