"""McpLivenessProbe — parallel liveness checks for MCP server configs."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

from stackowl.config.test_mode import TestModeGuard
from stackowl.mcp.allowlist import McpServerConfig

log = logging.getLogger("stackowl.mcp")


class McpLivenessProbe:
    """Parallel liveness probe for a list of MCP server configs.

    Each probe has a per-server timeout (from config).  probe_one() never raises
    — it always returns bool.
    """

    async def probe_all(self, configs: list[McpServerConfig]) -> dict[str, bool]:
        """Probe each server in parallel; return {name: is_alive}."""
        log.debug(
            "mcp.probe.probe_all: entry",
            extra={"_fields": {"server_count": len(configs)}},
        )
        # decision — gather all probes concurrently
        results = await asyncio.gather(
            *[self._probe_and_label(c) for c in configs]
        )
        outcome: dict[str, bool] = dict(results)
        log.debug(
            "mcp.probe.probe_all: exit",
            extra={"_fields": {"alive": sum(v for v in outcome.values())}},
        )
        return outcome

    async def _probe_and_label(self, config: McpServerConfig) -> tuple[str, bool]:
        alive = await self.probe_one(config)
        return config.name, alive

    async def probe_one(self, config: McpServerConfig) -> bool:
        """Probe a single server; return True if alive, False otherwise.

        Never raises — all exceptions are caught and logged.
        """
        log.debug(
            "mcp.probe.probe_one: entry",
            extra={"_fields": {"server": config.name}},
        )
        # guard — block in test mode
        TestModeGuard.assert_not_test_mode("mcp.probe")
        try:
            alive = await self._check(config)
        except Exception as exc:
            log.error(
                "mcp.probe.probe_one: unexpected error",
                exc_info=exc,
                extra={"_fields": {"server": config.name}},
            )
            alive = False
        log.debug(
            "mcp.probe.probe_one: exit",
            extra={"_fields": {"server": config.name, "alive": alive}},
        )
        return alive

    async def _check(self, config: McpServerConfig) -> bool:
        """Inner check logic; may raise — probe_one catches everything."""
        uri = config.uri
        timeout = config.timeout_seconds

        if uri.startswith("sse://"):
            # decision — strip scheme prefix to get the real HTTP URL
            http_url = uri[len("sse://"):]
            log.debug(
                "mcp.probe._check: using SSE strategy",
                extra={"_fields": {"server": config.name}},
            )
            async with httpx.AsyncClient() as client:
                resp = await client.get(http_url, timeout=timeout)
            # step — got response
            log.debug(
                "mcp.probe._check: SSE response received",
                extra={"_fields": {"server": config.name, "status": resp.status_code}},
            )
            return resp.status_code < 500

        if uri.startswith("stdio://"):
            # decision — check filesystem executable
            path_str = uri[len("stdio://"):]
            log.debug(
                "mcp.probe._check: using stdio strategy",
                extra={"_fields": {"server": config.name}},
            )
            p = Path(path_str)
            exists = p.exists()
            executable = exists and os.access(str(p), os.X_OK)
            log.debug(
                "mcp.probe._check: stdio check done",
                extra={"_fields": {"server": config.name, "exists": exists, "executable": executable}},
            )
            return executable

        # unknown scheme — deny
        log.warning(
            "mcp.probe._check: unknown URI scheme",
            extra={"_fields": {"server": config.name}},
        )
        return False
