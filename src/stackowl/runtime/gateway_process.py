"""Gateway process entrypoint — the durable client-facing half of the split.

Re-enters the standard boot through :class:`StartupOrchestrator` with
``role="gateway"``: it keeps the channel adapters + TUI, binds the unix-domain
socket, spawns the core subprocess, and routes every inbound message to the core
(and the core's output back to the adapters) via :class:`GatewayLink`. It does
NOT run the scheduler / MCP / browser / durable recovery — the core owns those,
so they never run twice.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.startup.orchestrator import StartupOrchestrator


async def run_gateway() -> None:
    """Boot StackOwl in gateway role: serve clients, forward turns to the core."""
    log.gateway.info("[ipc] gateway process: starting")
    await StartupOrchestrator(role="gateway").run()
    log.gateway.info("[ipc] gateway process: exited")
