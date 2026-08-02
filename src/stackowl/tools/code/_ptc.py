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
    " The code may call ANY of this agent's tools via 'import owl' — including "
    "ones that act on the outside world (send a message, create an owl or tool, "
    "write files) — WITHOUT asking again. Only sandbox-escape tools (shell, "
    "execute_code, process, claude_code, delegate_task, sessions_*) are refused. "
    "Approving this run approves everything the script does."
)


def ptc_enabled() -> bool:
    """PTC is on by DEFAULT when a tool registry is wired.

    D05.5 — this docstring used to justify the default with "the allowlist is
    read + sandbox-workspace-write only". That is no longer true: PTC is now
    default-ALLOW minus sandbox-escape vectors, so the default is wider than the
    one that reasoning defended. It stays ON by operator decision; the guardrails
    that remain are the escape denylist, owl bounds, write-confinement, the
    per-run rate cap and the audit log — plus the consent note above, which now
    tells the user that approving the run approves everything the script does.
    With no registry wired there is nothing to call back into, so PTC is absent.
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
    session_key = str(trace.get("session_key") or spec.session_key or "")
    trace_id = trace.get("trace_id")
    trace_id_str = str(trace_id) if trace_id is not None else None

    def _factory(workspace: Path, socket_path: Path) -> PtcServer:
        return PtcServer(
            registry=registry,
            workspace=workspace,
            socket_path=socket_path,
            session_key=session_key,
            trace_id=trace_id_str,
            audit_logger=audit,
        )

    log.tool.debug(
        "execute_code.execute: PTC factory built (host-tool callback enabled)",
        extra={"_fields": {"session": session_key or "-"}},
    )
    return _factory
