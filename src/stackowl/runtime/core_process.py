"""Core process entrypoint — the restartable agent half of the split.

Re-enters the standard boot through :class:`StartupOrchestrator` with
``role="core"``: it runs the *entire* existing pipeline (providers, owls, tools,
backend, scheduler, MCP, browser, memory) but its only "channel" is the socket
to the durable gateway. On a code change this is the process that drains and
exec-replaces itself; the gateway — and the TUI scrollback — never dies.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.startup.orchestrator import StartupOrchestrator


async def run_core() -> None:
    """Boot StackOwl in core role and serve turns over the gateway socket."""
    log.gateway.info("[ipc] core process: starting")
    await StartupOrchestrator(role="core").run()
    log.gateway.info("[ipc] core process: exited")
