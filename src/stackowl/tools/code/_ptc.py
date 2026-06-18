"""PTC factory + consent-disclosure helpers for ExecuteCodeTool (B2 ≤300 split).

Extracted from :mod:`stackowl.tools.code.execute_code` to keep the tool ≤300,
mirroring the ``_consent`` extraction. Owns the OPTIONAL PTC (host-tool callback)
wiring for one ``execute_code`` run: whether PTC is enabled, the per-run
:class:`~stackowl.sandbox.ptc.server.PtcServer` factory the backend starts, and the
consent-prompt disclosure sentence. Behaviour + consent text are byte-for-byte what
the tool produced inline before the split.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.sandbox.ptc.server import PtcServer
from stackowl.sandbox.spec import ExecSpec

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.sandbox.base import PtcFactory

__all__ = ["build_ptc_factory", "consent_callback_note", "ptc_enabled"]

# The consent-prompt sentence disclosing the curated host-tool callback (GAP-A). Shown
# only when PTC is enabled so the user consents to that capability, not just the run.
_CONSENT_CALLBACK_NOTE = (
    " The code may call a CURATED set of host tools (read a file, search the "
    "web, read/search memory, and write/edit files INSIDE the sandbox "
    "workspace only) via 'import owl'."
)


def ptc_enabled() -> bool:
    """PTC is on by DEFAULT when a tool registry is wired (the safer default).

    The allowlist is read + sandbox-workspace-write only and the hard-exclusions
    (shell/execute_code/process/…) are enforced HOST-side regardless. With no
    registry wired there is nothing to call back into, so PTC is simply absent.
    """
    return get_services().tool_registry is not None


def consent_callback_note() -> str:
    """The consent disclosure for the host-tool callback ('' when PTC is off)."""
    return _CONSENT_CALLBACK_NOTE if ptc_enabled() else ""


def build_ptc_factory(spec: ExecSpec) -> PtcFactory | None:
    """Build the per-run PtcServer factory, or None when PTC is unavailable.

    The factory takes the run's SANDBOX workspace dir (the backend supplies it)
    and returns a PtcServer bound to the HOST tool registry + audit logger,
    confined to that workspace, with the socket placed inside it. The backend owns
    start/serve/teardown. None → the isolation-only path (no host-tool callback).
    """
    services = get_services()
    registry = services.tool_registry
    if registry is None:
        return None
    audit = services.audit_logger
    trace = TraceContext.get()
    session_id = str(trace.get("session_id") or spec.session_id or "")
    trace_id = trace.get("trace_id")
    trace_id_str = str(trace_id) if trace_id is not None else None

    def _factory(workspace: Path, socket_path: Path) -> PtcServer:
        return PtcServer(
            registry=registry,
            workspace=workspace,
            socket_path=socket_path,
            session_id=session_id,
            trace_id=trace_id_str,
            audit_logger=audit,
        )

    log.tool.debug(
        "execute_code.execute: PTC factory built (host-tool callback enabled)",
        extra={"_fields": {"session": session_id or "-"}},
    )
    return _factory
