"""ExecuteCodeTool — run code in an ISOLATED sandbox (E11-S5, the KEYSTONE tool).

The consequential surface over the E11 sandbox substrate: builds an
:class:`~stackowl.sandbox.spec.ExecSpec`, asks the DI
:class:`~stackowl.sandbox.selector.SandboxSelector` which backend to use (bwrap-
primary; Docker only for a network run), runs there, returns a structured result.

Load-bearing invariants:

* **NEVER host-exec fallback.** No viable backend (selector unavailable / not
  wired) → a structured "code execution unavailable" result; NOTHING runs on the
  host. There is no degraded path.
* **Consent-gated (always-ask).** Consequential + on the always-ask list, so the
  gate fires BEFORE ``execute`` and cannot be batch/window-relaxed. The tool does
  not call the gate (dispatch does); it supplies a per-call :meth:`consent_summary`
  (GAP-A) showing the language + a bounded code DIGEST + whether network is asked.
* **Child-excluded (GAP-B).** A delegated sub-agent (depth>0) is refused this tool
  at the dispatch layer (``_CHILD_EXCLUDED_TOOLS``).
* **Python-only (MVP)**; **self-healing (B5)** — selector-None / unavailable /
  backend error → structured result, logged, NEVER raises.

Sensitive-data: logs record code LENGTH + language + network + backend, never the
code content (the consent prompt is the one trusted place the code is shown). The
returned stdout/stderr are the user's OWN output (byte-capped by ExecResult).
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps
from stackowl.tools.base import Tool, ToolManifest, ToolResult

__all__ = ["ExecuteCodeTool"]

_TOOLSET_GROUP = "code"
# Bounded code digest shown in the consent prompt (GAP-A): enough for the user to
# judge what runs, never an unbounded dump. The gate truncates again defensively.
_CONSENT_CODE_MAX_LINES = 40
_CONSENT_CODE_MAX_CHARS = 1000


class ExecuteCodeArgs(BaseModel):
    """Validated arguments for one ``execute_code`` invocation.

    The model may NOT request unbounded resources: caps come from the spec
    defaults (the mandatory non-zero rails). Only ``timeout_s`` is tunable here and
    a backend clamps it to its wall-time cap.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    language: str = "python"
    network: bool = False
    timeout_s: int | None = Field(default=None, ge=1)


class ExecuteCodeTool(Tool):
    """Run code in an isolated sandbox (consequential, consent-gated, never host)."""

    @property
    def name(self) -> str:
        return "execute_code"

    @property
    def description(self) -> str:
        return (
            "Run code in an ISOLATED sandbox (rootless, no host filesystem, no "
            "network unless you opt in) and get back its stdout/stderr/exit code. "
            "Use to actually RUN python — compute, reproduce a bug, verify a fix, "
            "transform data. Args: 'code' (required python); 'network'=true ONLY if "
            "the code must reach the internet (still isolated); 'timeout_s' optional. "
            "python only. CONSEQUENTIAL: the user is shown the code and approves "
            "before every run — write code safe to show and run. No sandbox backend "
            "available → returns 'unavailable'; nothing runs on the host."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The source code to run (python).",
                },
                "language": {
                    "type": "string",
                    "enum": ["python"],
                    "description": "Source language (python only for now).",
                },
                "network": {
                    "type": "boolean",
                    "description": (
                        "Allow the code network access. Default false (no network). "
                        "Set true ONLY when the code must reach the internet."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional wall-time budget in seconds.",
                },
            },
            "required": ["code"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            toolset_group=_TOOLSET_GROUP,
        )

    def consent_summary(self, **call_args: object) -> str | None:
        """Render the TRUSTED, bounded consent digest for THIS call (GAP-A).

        Shows the LANGUAGE + whether NETWORK is requested + a bounded slice of the
        actual CODE so the user consents to what will really run — never the generic
        description. Bounded (the gate truncates again). Never raises — best-effort
        even when validation will reject the call, so the prompt is never blank.
        """
        try:
            args = ExecuteCodeArgs.model_validate(call_args)
            language, network, code = args.language, args.network, args.code
        except ValidationError:
            raw = call_args.get("code")
            code = raw if isinstance(raw, str) else ""
            language = str(call_args.get("language") or "python")
            network = bool(call_args.get("network"))
        net = "WITH network access" if network else "no network"
        return (
            f"Run this {language} code in an isolated sandbox ({net}):\n"
            f"```\n{self._bounded_code(code)}\n```"
        )

    @staticmethod
    def _bounded_code(code: str) -> str:
        """Bound the code to the first N lines / chars with an honest elision note."""
        lines = code.splitlines()
        clipped = False
        if len(lines) > _CONSENT_CODE_MAX_LINES:
            lines = lines[:_CONSENT_CODE_MAX_LINES]
            clipped = True
        digest = "\n".join(lines)
        if len(digest) > _CONSENT_CODE_MAX_CHARS:
            digest = digest[:_CONSENT_CODE_MAX_CHARS]
            clipped = True
        if clipped:
            digest = f"{digest}\n…[code truncated for display]"
        return digest

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY — log SHAPE only, never the code content (length only).
        log.tool.info(
            "execute_code.execute: entry",
            extra={"_fields": {
                "code_len": len(str(kwargs.get("code") or "")),
                "language": kwargs.get("language", "python"),
                "network": bool(kwargs.get("network")),
            }},
        )
        try:
            args = ExecuteCodeArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "execute_code.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.errors()!r}", t0)

        # 2. DECISION — python-only for the MVP; refuse anything else clearly.
        if args.language != "python":
            return self._err(
                f"language {args.language!r} is not supported yet — only 'python' "
                "code can be executed.",
                t0,
            )

        # The selector is the SINGLE source of truth for which backend runs (and
        # whether ANY can). No selector wired → unavailable, NEVER host exec.
        selector = get_services().sandbox_selector
        if selector is None:
            log.tool.error("execute_code.execute: no sandbox_selector wired — refusing")
            return self._err(
                "code execution unavailable — no sandbox backend is wired. Code is "
                "NEVER run on the host. Install bubblewrap (bwrap) or Docker, or "
                "enable one in settings.",
                t0,
            )

        spec = self._build_spec(args)
        selection = selector.select(spec)
        if not selection.available or selection.backend is None:
            reason = selection.reason or "no sandbox backend available"
            log.tool.warning(
                "execute_code.execute: selector unavailable — refusing (no host exec)",
                extra={"_fields": {"reason": reason}},
            )
            return self._err(f"code execution unavailable — {reason}", t0)

        backend = selection.backend
        log.tool.info(
            "execute_code.execute: backend selected",
            extra={"_fields": {"backend": backend.name, "network": spec.network}},
        )

        # 3. STEP — run in the isolated backend. The backend NEVER raises (B5), but
        # belt-and-braces wrap it so a contract breach degrades to a structured result.
        try:
            result = await backend.run(spec)
        except Exception as exc:  # B5 — contract says never, but never trust+host-exec
            log.tool.error(
                "execute_code.execute: backend.run raised — refusing (no host exec)",
                exc_info=exc,
                extra={"_fields": {"backend": backend.name}},
            )
            return self._err(
                f"code execution failed in the sandbox ({type(exc).__name__}); "
                "nothing was run on the host.",
                t0,
            )

        return self._ok(result, t0)

    @staticmethod
    def _build_spec(args: ExecuteCodeArgs) -> ExecSpec:
        """Build the ExecSpec — mandatory non-zero caps from the spec defaults.

        The model never sets caps directly (the rails are not negotiable); only
        ``timeout_s`` is forwarded (a backend clamps it to its wall-time cap). The
        session_id correlates the audit trail; backends log it, never trust it.
        """
        session_id = str(TraceContext.get().get("session_id") or "")
        spec_kwargs: dict[str, object] = {
            "code": args.code, "language": "python", "network": args.network,
            "caps": ResourceCaps(), "session_id": session_id,
        }
        if args.timeout_s is not None:
            spec_kwargs["timeout_s"] = args.timeout_s
        return ExecSpec(**spec_kwargs)  # type: ignore[arg-type]

    def _ok(self, result: ExecResult, t0: float) -> ToolResult:
        """Map a provenance-tagged ExecResult into a structured ToolResult.

        ``success`` reflects whether the RUN itself completed (the program's own
        non-zero exit is surfaced in the payload, not a tool failure) — a sandbox
        denial / error is a tool failure. Never raises.
        """
        record: dict[str, object] = {
            "stdout": result.stdout, "stderr": result.stderr,
            "exit_code": result.exit_code, "exit_reason": result.exit_reason,
            "backend": result.backend_used, "network_enabled": result.network_enabled,
            "caps": result.caps_applied.model_dump(),
            "truncated": result.stdout_truncated or result.stderr_truncated,
            "duration_ms": result.duration_ms,
        }
        # A sandbox-level failure (denied / sandbox_error / oom / killed / timeout)
        # is a tool failure; "ok" (the program ran, whatever its exit code) succeeds.
        run_completed = result.exit_reason == "ok"
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.tool.info(
            "execute_code.execute: exit",
            extra={"_fields": {
                "success": run_completed, "exit_reason": result.exit_reason,
                "exit_code": result.exit_code, "backend": result.backend_used,
                "duration_ms": duration_ms,
            }},
        )
        payload = json.dumps({"record": record}, ensure_ascii=False)
        if run_completed:
            return ToolResult(success=True, output=payload, duration_ms=duration_ms)
        error = f"sandbox run {result.exit_reason}: {result.stderr or '(no detail)'}"
        return ToolResult(
            success=False, output=payload, error=error, duration_ms=duration_ms
        )

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        """Structured FAILED result (the model learns nothing ran). Never raises."""
        msg = f"execute_code: {msg}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "execute_code.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
