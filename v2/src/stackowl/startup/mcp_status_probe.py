"""McpStatusProbe — truthful liveness check for the MCP SSE server (OPS-3 / F148).

The ``mcp status`` CLI command used to print "not running" unconditionally even
while the in-process SSE server was up. This probe does a real, bounded TCP
connect against the configured ``host:port`` so the command can report the
actual state. It NEVER asserts "not running" while the port accepts a
connection. Cross-platform: a plain ``socket.create_connection`` with a short
timeout, no OS-specific PID/process inspection (the SSE server is in-process
under ``serve`` and may also be an externally launched ``mcp start``).
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

log = logging.getLogger("stackowl.startup")

_DEFAULT_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class McpStatusResult:
    """Outcome of a single MCP liveness probe."""

    host: str
    port: int
    running: bool
    reason: str | None


class McpStatusProbe:
    """Probe whether an MCP SSE server is accepting connections at host:port."""

    def __init__(self, host: str, port: int, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._host = host
        self._port = port
        self._timeout_s = timeout_s

    def check(self) -> McpStatusResult:
        """Attempt a bounded TCP connect; report running iff the port answers."""
        log.debug(
            "[startup] mcp_status_probe.check: entry",
            extra={"_fields": {"host": self._host, "port": self._port}},
        )
        try:
            with socket.create_connection((self._host, self._port), timeout=self._timeout_s):
                log.info(
                    "[startup] mcp_status_probe.check: exit — running",
                    extra={"_fields": {"host": self._host, "port": self._port}},
                )
                return McpStatusResult(
                    host=self._host, port=self._port, running=True, reason=None
                )
        except OSError as exc:
            # Connection refused / timeout / unreachable — the server is not
            # accepting connections here. Report honestly with the reason; this
            # is the ONLY path that may say "not running".
            log.debug(
                "[startup] mcp_status_probe.check: exit — not running",
                extra={"_fields": {"host": self._host, "port": self._port, "error": str(exc)}},
            )
            return McpStatusResult(
                host=self._host, port=self._port, running=False, reason=str(exc)
            )
