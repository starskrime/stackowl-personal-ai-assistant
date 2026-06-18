"""MCP boot phase — discover + register federated tools, fail-soft (E1-S3 / ADR-6).

Runs after providers and before the gateway accepts traffic. Fans out per-server
discovery concurrently with ``asyncio.gather`` (asyncio-native — no background
thread), enforces a per-server timeout, and is FAIL-SOFT: a down or slow server
logs a warning and boot continues. Tools register under ``mcp.<server>.<tool>``
(non-clobbering vs first-party tools — see McpTool.name).

Provenance: see ``_bmad-output/research/tool-port-analysis.md`` (E1 MCP-client
wiring) — the discover→parallel→register→summary lifecycle pattern, re-expressed
on asyncio.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.mcp.allowlist import McpServerConfig
    from stackowl.tools.registry import ToolRegistry

__all__ = ["run"]

_DEFAULT_TIMEOUT_SECONDS = 5.0


class _SupportsRegister(Protocol):
    async def register_server_tools(self, config: McpServerConfig, tool_registry: ToolRegistry) -> int: ...


async def run(
    client: _SupportsRegister,
    configs: list[McpServerConfig],
    tool_registry: ToolRegistry,
    *,
    default_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Register tools from every enabled MCP server. Returns {server: tool_count}.

    Never raises on a server failure — a bad/slow server contributes 0 and boot
    proceeds. Only ``asyncio.CancelledError`` propagates (cooperative shutdown).
    """
    # 1. ENTRY
    enabled = [c for c in configs if c.enabled]
    log.engine.info(
        "[startup] mcp_register: entry",
        extra={"_fields": {"configured": len(configs), "enabled": len(enabled)}},
    )
    if not enabled:
        return {}

    async def _one(config: McpServerConfig) -> tuple[str, int]:
        timeout = config.timeout_seconds or default_timeout
        try:
            count = await asyncio.wait_for(
                client.register_server_tools(config, tool_registry), timeout=timeout
            )
            log.engine.debug(
                "[startup] mcp_register: server ok",
                extra={"_fields": {"server": config.name, "tools": count}},
            )
            return config.name, count
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            log.engine.warning(
                "[startup] mcp_register: server timed out — skipping (fail-soft)",
                extra={"_fields": {"server": config.name, "timeout_s": timeout}},
            )
            return config.name, 0
        except Exception as exc:
            log.engine.warning(
                "[startup] mcp_register: server failed — skipping (fail-soft)",
                exc_info=exc,
                extra={"_fields": {"server": config.name}},
            )
            return config.name, 0

    # 3. STEP — fan out
    results = await asyncio.gather(*[_one(c) for c in enabled])
    summary = dict(results)
    # 4. EXIT
    log.engine.info(
        "[startup] mcp_register: exit",
        extra={"_fields": {"servers": len(summary), "tools_total": sum(summary.values())}},
    )
    return summary
