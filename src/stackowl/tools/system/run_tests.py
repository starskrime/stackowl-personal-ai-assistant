"""RunTestsTool — run a test suite on the HOST and return a STRUCTURED result.

Mirrors :class:`~stackowl.tools.code.execute_code.ExecuteCodeTool`'s result-
shaping philosophy (a structured record, never raw stdout dumped as the
answer) but runs on the HOST via the shared subprocess seam
(:func:`stackowl.tools.system.shell.run_argv`) instead of a sandbox — a test
suite needs to see the actual repo checkout (installed deps, fixtures, a
worktree from ``claude_code`` isolation), which an isolated sandbox has no
access to.

Parsing is BEST-EFFORT and honestly labeled, not a universal per-framework
integration (ponytail: real ceiling, documented upgrade path). It recognizes
the "N passed, M failed" / "N passing, M failing" style summary line common to
pytest/jest/mocha-shaped runners, and pytest's ``FAILED <name> - <reason>``
short-summary lines for a per-failure list. Anything it can't parse still gets
an honest pass/fail from the exit code, plus a bounded raw tail — never a
fabricated "0 failures" when parsing simply didn't recognize the format.

This is the producer of the REAL ``verified`` signal the Phase 1 aggregation
(:func:`stackowl.pipeline.acceptance_authority.aggregate_verdicts`) combines
across an epic's steps: it declares a
:class:`~stackowl.pipeline.acceptance_authority.TestsPassed` post-condition
built from these same parsed counts.
"""

from __future__ import annotations

import json
import re
import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.system.shell import _default_workspace_cwd, run_argv

__all__ = ["RunTestsTool"]

_TOOLSET_GROUP = "code"
_TIMEOUT_SEC = 300.0
_TIMEOUT_CEILING_SEC = 1800.0
_MAX_FAILURES = 30
_MAX_RAW_TAIL_CHARS = 3000

# Aliases normalized to one of: passed, failed, errors, skipped, xfailed, xpassed.
_CATEGORY_ALIASES = {
    "passed": "passed", "passing": "passed",
    "failed": "failed", "failing": "failed",
    "error": "errors", "errors": "errors",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
}
_SUMMARY_RE = re.compile(
    r"(\d+)\s+(passed|passing|failed|failing|errors?|skipped|xfailed|xpassed)\b", re.IGNORECASE,
)
# pytest's short-summary-info lines: "FAILED path::test - reason" / "ERROR path::test - reason".
_FAILURE_LINE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)(?:\s*-\s*(.*))?$", re.MULTILINE)


def _resolve_timeout(raw: object) -> float:
    if raw is None:
        return _TIMEOUT_SEC
    try:
        requested = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _TIMEOUT_SEC
    if requested <= 0:
        return _TIMEOUT_SEC
    return min(requested, _TIMEOUT_CEILING_SEC)


def _parse_summary_counts(output: str) -> dict[str, int]:
    """Best-effort: sum the LAST occurrence of each category in the tail of output.

    Scoped to the final 2000 chars so a coincidental "3 passed" inside a
    traceback/assertion message earlier in the log is not mistaken for the
    run's real summary — the summary line is always at (or near) the end.
    """
    tail = output[-2000:]
    counts: dict[str, int] = {}
    for match in _SUMMARY_RE.finditer(tail):
        n, raw_cat = int(match.group(1)), match.group(2).lower()
        category = _CATEGORY_ALIASES[raw_cat]
        counts[category] = n  # last match per category wins (closest to summary line)
    return counts


_SHORT_SUMMARY_MARKER = "short test summary info"


def _parse_failures(output: str) -> list[dict[str, str]]:
    """Extract FAILED/ERROR name+reason lines — scoped to AFTER pytest's own
    "short test summary info" header when present, so an arbitrary tool that
    happens to print an unrelated line starting with "ERROR " is never
    mistaken for a failing test name. Falls back to the whole text when the
    marker is absent (a non-pytest runner that still emits this line shape)."""
    marker_idx = output.rfind(_SHORT_SUMMARY_MARKER)
    scoped = output[marker_idx:] if marker_idx != -1 else output
    failures: list[dict[str, str]] = []
    for match in _FAILURE_LINE_RE.finditer(scoped):
        name, reason = match.group(1), (match.group(2) or "").strip()
        failures.append({"name": name, "message": reason})
        if len(failures) >= _MAX_FAILURES:
            break
    return failures


class RunTestsTool(Tool):
    """Run a test command and return a structured pass/fail record."""

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Run a test command and get back a STRUCTURED pass/fail record — "
            "counts + a bounded failure list, never raw stdout. Args: 'command' "
            "(required) the exact command to run, e.g. 'uv run pytest "
            "tests/foo.py -q'; 'workdir' (defaults to the StackOwl workspace); "
            "'timeout' seconds. The tool call itself succeeds whenever the test "
            "command RAN to completion (whether tests passed or failed is in the "
            "returned record's 'all_passed'/'failed' fields) — it only fails when "
            "the command never produced a result (bad workdir, timeout, command "
            "not found)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The exact test command to run (shell syntax, e.g. "
                        "'uv run pytest tests/foo.py -q')."
                    ),
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory (defaults to the StackOwl workspace).",
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        f"Per-run timeout in seconds (default {int(_TIMEOUT_SEC)}, "
                        f"bounded to {int(_TIMEOUT_CEILING_SEC)})."
                    ),
                },
            },
            "required": ["command"],
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
        command = str(kwargs.get("command", "")).strip()
        workdir = str(kwargs.get("workdir", "")) or _default_workspace_cwd()
        timeout_sec = _resolve_timeout(kwargs.get("timeout"))
        # 1. ENTRY
        log.tool.debug(
            "run_tests.execute: entry",
            extra={"_fields": {"command": command[:200], "workdir": workdir}},
        )
        if not command:
            return self._err("no command given", t0, committed=False)

        # 2. STEP — real shell mode (chaining/pipes work, e.g. "cd sub && pytest").
        result = await run_argv(
            command.split(), tool_name="run_tests", workdir=workdir,
            timeout_sec=timeout_sec, shell_command=command, intent="write",
        )

        # DECISION — empty output + a failed run means the command never actually
        # produced a test report (spawn error, timeout, "command not found"): a
        # genuine tool failure, not a parseable test outcome.
        if not result.success and not result.output.strip():
            log.tool.warning(
                "run_tests.execute: command never produced output — treating as tool failure",
                extra={"_fields": {"error": (result.error or "")[:200]}},
            )
            return result

        combined = result.output + ("\n" + result.error if result.error else "")
        counts = _parse_summary_counts(combined)
        failures = _parse_failures(combined)
        total = sum(counts.get(k, 0) for k in ("passed", "failed", "errors", "xfailed", "xpassed"))
        failed = counts.get("failed", 0)
        errors = counts.get("errors", 0)

        framework = ("pytest" if failures else "summary_counts") if total > 0 else "unknown"

        all_passed = total > 0 and failed == 0 and errors == 0 and result.success
        record: dict[str, object] = {
            "framework": framework,
            "total": total,
            "passed": counts.get("passed", 0),
            "failed": failed,
            "errors": errors,
            "skipped": counts.get("skipped", 0),
            "all_passed": all_passed,
            "failures": failures,
            "exit_success": result.success,
        }
        if framework == "unknown":
            record["raw_tail"] = combined[-_MAX_RAW_TAIL_CHARS:]

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT — the RUN completed (whatever it found); tool-level success=True.
        log.tool.debug(
            "run_tests.execute: exit",
            extra={"_fields": {
                "framework": framework, "total": total, "failed": failed,
                "errors": errors, "all_passed": all_passed, "duration_ms": duration_ms,
            }},
        )
        return ToolResult(
            success=True, output=json.dumps(record, ensure_ascii=False),
            duration_ms=duration_ms, side_effect_committed=True,
        )

    def post_condition(self, args: dict[str, object], result: ToolResult) -> object | None:
        """Declare TestsPassed from THIS run's own parsed counts (ADR-1).

        The authority re-reads the same structured record ``execute`` already
        produced — not a second, independent test run — so it observes the
        parsed evidence rather than trusting ``result.success`` (which is True
        whenever the command ran, pass or fail). See module docstring.
        """
        from stackowl.pipeline.acceptance_authority import TestsPassed

        try:
            record = json.loads(result.output)
        except (json.JSONDecodeError, ValueError) as exc:
            # Should never happen — execute() built this exact JSON itself — but
            # per repo convention, never leave an except silent: log it so a
            # future refactor that breaks this invariant is debuggable.
            log.tool.warning(
                "run_tests.post_condition: own output was not valid JSON — treating as undeclared",
                extra={"_fields": {"err": type(exc).__name__}},
            )
            return None
        if not isinstance(record, dict) or record.get("total", 0) == 0:
            return None
        return TestsPassed(
            total=int(record.get("total", 0)),
            failed=int(record.get("failed", 0)),
            errors=int(record.get("errors", 0)),
            framework=str(record.get("framework", "unknown")),
        )

    @staticmethod
    def _err(msg: str, t0: float, *, committed: bool = True) -> ToolResult:
        msg = f"run_tests: {msg}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "run_tests.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=msg, duration_ms=duration_ms, side_effect_committed=committed,
        )
