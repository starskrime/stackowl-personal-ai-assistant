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
from stackowl.tools.verification import is_trustworthy_success

__all__ = ["PtcToolInvoker"]


class PtcToolInvoker:
    """Runs one allowlisted host tool for a PTC request (bounds + confine + audit)."""

    def __init__(
        self,
        *,
        registry: object,
        workspace: object,
        session_key: str,
        trace_id: str | None,
        audit_logger: object | None,
        limits: PtcLimits,
    ) -> None:
        self._registry = registry
        self._workspace = workspace
        self._session_key = session_key
        self._trace_id = trace_id
        self._audit = audit_logger
        self._limits = limits

    # ----------------------------------------------------------------- invoke
    async def invoke(self, tool: str, args: dict[str, object]) -> dict[str, object]:
        """Resolve + run the host tool under the run's trace. Returns a result dict.

        Write tools (``write_file``/``edit``) are confined to the SANDBOX workspace via
        the path_guard root override (defense-in-depth: the path is independently
        re-resolved + escape-checked first).

        THE TOOL IS CALLED, not reached past into ``execute()`` — corrected 2026-08-20,
        and the reasons the old bypass gave were both wrong. It said consent must not
        be re-prompted: there is NO consent gate in ``Tool.__call__``, consent lives in
        the pipeline dispatch, so that reason described something that never happened.
        It said the bypass makes PTC work under a test-mode guard: ``TestModeGuard``
        exists to block live I/O when the platform runs in test mode, and a sandboxed
        script calling a real host tool IS live I/O — so that was a hole around the
        guard, not a feature of the design.
        What the bypass actually cost: ``verify()``, the ACCEPTANCE AUTHORITY, the
        exception wrapper and the lifecycle hooks. PTC is default-ALLOW minus the
        escape vectors below, so ``send_message``, ``owl_build`` and ``skill_manage``
        are reachable from a script — which is where an unverified success matters
        most, not least. The verdict now rides back to the script in ``verified``.

        D05.5 — THE STATED REASON FOR THAT BYPASS IS NO LONGER TRUE, and is
        rewritten here rather than left standing. It used to read "the allowlist
        is read+workspace-write only", which made skipping consent safe by
        construction. PTC is now default-ALLOW minus sandbox-escape vectors
        (:data:`PTC_DENYLIST`), so consequential tools — send_message, owl_build,
        tool_build, skill_manage, send_file — ARE reachable from a script and are
        NOT prompted. execute_code's own consent is now the ONLY consent covering
        everything a script does. Operator decision, taken with that consequence
        stated. What still constrains a call: owl bounds ∩ creation_ceiling,
        write-confinement, arg bounds, the per-run rate cap, and the audit log.
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

        token = TraceContext.start(session_key=self._session_key, trace_id=self._trace_id)
        try:
            if tool in PTC_WRITE_TOOLS:
                with sandbox_write_root(self._workspace):  # type: ignore[arg-type]
                    result = await instance(**call_args)
            else:
                result = await instance(**call_args)
        finally:
            TraceContext.reset(token)
        verified = getattr(result, "verified", None)
        # is_trustworthy_success is the ONE predicate: verified None falls back to the
        # self-report (byte-identical for every tool that does not verify), and a
        # measured-absent effect is never a win. The script is told the verdict too,
        # so it can act on "claimed but unconfirmed" rather than only on success.
        succeeded = is_trustworthy_success(
            bool(getattr(result, "success", False)), verified,
        )
        error = getattr(result, "error", None)
        if not succeeded and not error:
            error = (
                f"'{tool}' reported success but the platform could not confirm the "
                "effect happened"
            )
        return {
            "success": succeeded,
            "output": getattr(result, "output", "") if succeeded else "",
            "error": error,
            "verified": verified,
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
                f"sandbox:{self._session_key or '-'}",
                tool,
                {"allowed": allowed, "reason": reason, "arg_keys": sorted(args.keys())},
            )
            log.tool.debug("[sandbox.ptc] audited", extra={"_fields": {"tool": tool, "allowed": allowed}})
