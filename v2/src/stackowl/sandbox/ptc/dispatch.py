"""PtcToolInvoker — resolves + runs an allowlisted host tool for a PTC call.

Split out of :class:`~stackowl.sandbox.ptc.server.PtcServer` (B2 ≤300) so the server
stays focused on socket lifecycle + framing + the allowlist/rate-limit POLICY, while
this class owns the actual host-tool invocation: argument-size bounds, write-tool
confinement to the SANDBOX workspace, running the real tool under the run's trace, and
the per-call audit. The invoker is the place the HOST trust boundary actually crosses
into a real tool, so every guard here is load-bearing and never trusts the caller.
"""

from __future__ import annotations

import contextlib

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.sandbox.ptc.confine import (
    confined_path_arg,
    read_target_protected,
    sandbox_write_root,
)
from stackowl.sandbox.ptc.protocol import PTC_WRITE_TOOLS, PtcLimits

__all__ = ["PtcToolInvoker"]


class PtcToolInvoker:
    """Runs one allowlisted host tool for a PTC request (bounds + confine + audit)."""

    def __init__(
        self,
        *,
        registry: object,
        workspace: object,
        session_id: str,
        trace_id: str | None,
        audit_logger: object | None,
        limits: PtcLimits,
    ) -> None:
        self._registry = registry
        self._workspace = workspace
        self._session_id = session_id
        self._trace_id = trace_id
        self._audit = audit_logger
        self._limits = limits

    # ----------------------------------------------------------------- invoke
    async def invoke(self, tool: str, args: dict[str, object]) -> dict[str, object]:
        """Resolve + run the host tool under the run's trace. Returns a result dict.

        Write tools (``write_file``/``edit``) are confined to the SANDBOX workspace via
        the path_guard root override (defense-in-depth: the path is independently
        re-resolved + escape-checked first). ``tool.execute`` is called DIRECTLY (not
        ``__call__``) so the per-call consent gate is NOT re-prompted — the
        execute_code consent already covered this run, the allowlist is
        read+workspace-write only — and so it also works under a test-mode guard.
        """
        get = getattr(self._registry, "get", None)
        instance = get(tool) if callable(get) else None
        if instance is None:
            return {"success": False, "error": f"host tool '{tool}' is not registered"}

        # read_file must not bulk-read the internal data stores (memory DB / vectors /
        # graph) — the memory tool gives curated recall; raw store reads are exfil.
        if tool == "read_file" and read_target_protected(args):
            return {
                "success": False,
                "error": (
                    "read_file may not read the internal data stores (conversation DB, "
                    "vectors, knowledge graph, secrets) from a sandbox — use the memory "
                    "tool for curated recall"
                ),
            }

        call_args = dict(args)
        if tool in PTC_WRITE_TOOLS:
            safe = confined_path_arg(args, self._workspace)  # type: ignore[arg-type]
            if safe is None:
                return {
                    "success": False,
                    "error": (
                        f"'{tool}' may only write inside the sandbox workspace; "
                        "the requested path is missing or escapes it"
                    ),
                }
            call_args["path"] = str(safe)

        token = TraceContext.start(session_id=self._session_id, trace_id=self._trace_id)
        try:
            if tool in PTC_WRITE_TOOLS:
                with sandbox_write_root(self._workspace):  # type: ignore[arg-type]
                    result = await instance.execute(**call_args)
            else:
                result = await instance.execute(**call_args)
        finally:
            TraceContext.reset(token)
        return {
            "success": bool(getattr(result, "success", False)),
            "output": getattr(result, "output", ""),
            "error": getattr(result, "error", None),
        }

    # ----------------------------------------------------------------- bounds
    def check_arg_bounds(self, tool: str, args: dict[str, object]) -> str | None:
        """Refuse oversized args (anti-DoS). Returns an error string or None."""
        for key, value in args.items():
            if isinstance(value, str) and len(value.encode("utf-8", "replace")) > self._limits.max_arg_bytes:
                return f"argument '{key}' exceeds the {self._limits.max_arg_bytes}-byte cap"
        if tool in {"web_search", "memory"}:
            text = args.get("query")
            if isinstance(text, str) and len(text) > self._limits.max_query_chars:
                return f"query exceeds the {self._limits.max_query_chars}-char cap"
        return None

    # ----------------------------------------------------------------- audit
    def audit(self, tool: str, args: dict[str, object], *, allowed: bool, reason: str) -> None:
        """Audit one PTC call — tool + BOUNDED arg KEY names, never secret VALUES."""
        append = getattr(self._audit, "append", None)
        if not callable(append):
            return
        with contextlib.suppress(Exception):  # B5 — audit failure never breaks a call
            append(
                "ptc_call",
                f"sandbox:{self._session_id or '-'}",
                tool,
                {"allowed": allowed, "reason": reason, "arg_keys": sorted(args.keys())},
            )
            log.tool.debug("[sandbox.ptc] audited", extra={"_fields": {"tool": tool, "allowed": allowed}})
