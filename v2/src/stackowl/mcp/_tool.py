"""McpTool — Tool ABC wrapper for MCP-discovered tools."""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import TYPE_CHECKING

from stackowl.mcp.allowlist import McpServerConfig
from stackowl.mcp.cache import McpToolDefinition
from stackowl.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from stackowl.mcp.client import McpClient

log = logging.getLogger("stackowl.mcp")

__all__ = ["McpTool", "sanitize_mcp_schema", "sanitize_mcp_text"]

_MAX_DESC_LEN = 500
_MAX_SCHEMA_DEPTH = 8
_MAX_SCHEMA_NODES = 500


def sanitize_mcp_text(text: str) -> str:
    """Neutralize untrusted MCP tool text before it enters the model's context.

    Language-neutral (no English keyword lists): strips Unicode control (Cc) and
    format (Cf — zero-width, bidi-override) characters that can hide injected
    instructions, collapses whitespace, and caps length. Normal multilingual text
    passes through unchanged. (E1-S3 party-mode: descriptions are a trust boundary.)
    """
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    cleaned = " ".join(cleaned.split())
    return cleaned[:_MAX_DESC_LEN]


def sanitize_mcp_schema(schema: object) -> dict[str, object]:
    """Sanitize an untrusted remote JSON-Schema before it reaches the provider.

    A tool's parameter schema is just as model-visible as its description: a
    malicious server can hide injected instructions in a property ``description``/
    ``title``/``enum`` value, or return a huge/deep schema to blow context. This
    recursively runs every string value through :func:`sanitize_mcp_text` and caps
    depth + total node count (over-budget branches are dropped). A non-dict input
    degrades to a safe empty object schema. (E1-S3 party-mode MAJOR #1.)
    """
    counter = {"n": 0}

    def _walk(node: object, depth: int) -> object | None:
        counter["n"] += 1
        if depth > _MAX_SCHEMA_DEPTH or counter["n"] > _MAX_SCHEMA_NODES:
            return None  # drop over-budget branch
        if isinstance(node, dict):
            out: dict[str, object] = {}
            for key, value in node.items():
                walked = _walk(value, depth + 1)
                if walked is not None:
                    out[str(key)[:_MAX_DESC_LEN]] = walked
            return out
        if isinstance(node, list):
            walked_items = [w for item in node if (w := _walk(item, depth + 1)) is not None]
            return walked_items
        if isinstance(node, str):
            return sanitize_mcp_text(node)
        if isinstance(node, bool) or node is None or isinstance(node, (int, float)):
            return node
        return sanitize_mcp_text(str(node))

    result = _walk(schema, 0)
    return result if isinstance(result, dict) else {"type": "object"}


class McpTool(Tool):
    """Wrapper that exposes an MCP-discovered tool through the standard Tool interface."""

    def __init__(
        self,
        definition: McpToolDefinition,
        client: McpClient,
        server_config: McpServerConfig,
    ) -> None:
        self._definition = definition
        self._client = client
        self._server_config = server_config

    @property
    def name(self) -> str:
        # Namespaced StackOwl-facing name (E1-S3 / §17): mcp.<server>.<tool> so a
        # federated tool can never clobber a first-party tool of the same raw name.
        return f"mcp.{self._definition.server_name}.{self._definition.name}"

    @property
    def description(self) -> str:
        return sanitize_mcp_text(self._definition.description)

    @property
    def parameters(self) -> dict[str, object]:
        # Remote schema is a trust boundary too — sanitize before it reaches the model.
        return sanitize_mcp_schema(self._definition.input_schema)

    async def execute(self, **kwargs: object) -> ToolResult:
        log.debug(
            "mcp_tool.execute: entry",
            extra={"_fields": {"tool": self.name, "arg_keys": list(kwargs.keys())}},
        )
        t0 = time.monotonic()
        try:
            # Invoke the RAW tool name on the server — the namespaced self.name is
            # the StackOwl registry key only; the remote server knows the raw name.
            result_str = await self._client.call_tool(
                self._server_config, self._definition.name, dict(kwargs)
            )
            duration_ms = (time.monotonic() - t0) * 1000
            log.debug(
                "mcp_tool.execute: exit",
                extra={"_fields": {"tool": self.name, "duration_ms": duration_ms}},
            )
            return ToolResult(success=True, output=result_str, duration_ms=duration_ms)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.error(
                "mcp_tool.execute: call failed",
                exc_info=exc,
                extra={"_fields": {"tool": self.name, "duration_ms": duration_ms}},
            )
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)
