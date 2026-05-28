"""ToolRegistry — holds all registered Tool instances."""

from __future__ import annotations

from collections.abc import Callable

from stackowl.infra.observability import log
from stackowl.tools.base import Tool


class ConsequentialActionGate:
    """Middleware that requires explicit YES confirmation for consequential tools.

    Pass a custom ``confirm_fn`` to override the default stdin prompt — useful
    for non-interactive channels (Telegram, tests) that need a different UX.
    """

    def __init__(self, confirm_fn: Callable[[str], bool] | None = None) -> None:
        # 1. ENTRY
        log.tool.debug("[gate] ConsequentialActionGate.__init__: entry")
        self._confirm_fn = confirm_fn or self._default_confirm
        # 2. DECISION — custom vs default
        log.tool.debug(
            "[gate] ConsequentialActionGate.__init__: exit",
            extra={"_fields": {"custom_fn": confirm_fn is not None}},
        )

    def _default_confirm(self, tool_name: str) -> bool:
        """Prompt on stdin; block in non-interactive environments."""
        import sys

        # 1. ENTRY
        log.tool.debug(
            "[gate] _default_confirm: entry",
            extra={"_fields": {"tool": tool_name}},
        )
        # 2. DECISION — check for TTY
        if not sys.stdin.isatty():
            log.tool.warning(
                "[gate] _default_confirm: non-interactive — blocking consequential action",
                extra={"_fields": {"tool": tool_name}},
            )
            return False
        # 3. STEP — prompt user
        answer = input(
            f"⚠ This action ({tool_name}) is consequential. Type YES to confirm: "
        )
        confirmed = answer.strip() == "YES"
        # 4. EXIT
        log.tool.debug(
            "[gate] _default_confirm: exit",
            extra={"_fields": {"tool": tool_name, "confirmed": confirmed}},
        )
        return confirmed

    def check(self, tool: Tool) -> bool:
        """Return True if execution should proceed.

        For non-consequential tools, always returns True without calling confirm_fn.
        For consequential tools, delegates to confirm_fn.
        """
        # 1. ENTRY
        log.tool.debug(
            "[gate] check: entry",
            extra={"_fields": {"tool": tool.name, "severity": tool.manifest.action_severity}},
        )
        # 2. DECISION — skip gate for non-consequential tools
        if tool.manifest.action_severity != "consequential":
            log.tool.debug(
                "[gate] check: exit — non-consequential, allowing",
                extra={"_fields": {"tool": tool.name}},
            )
            return True
        # 3. STEP — ask for confirmation
        confirmed = self._confirm_fn(tool.name)
        # 4. EXIT
        log.tool.debug(
            "[gate] check: exit",
            extra={"_fields": {"tool": tool.name, "confirmed": confirmed}},
        )
        return confirmed


class ToolRegistry:
    """Process-level registry of available tools."""

    def __init__(self, gate: ConsequentialActionGate | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._source_map: dict[str, list[str]] = {}
        self._gate = gate

    def register(self, tool: Tool, source_name: str | None = None) -> None:
        self._tools[tool.name] = tool
        if source_name:
            self._source_map.setdefault(source_name, []).append(tool.name)
        log.tool.debug(
            "[tools] registry.register: tool registered",
            extra={"_fields": {"tool": tool.name, "source": source_name}},
        )

    def unregister_by_source(self, source_name: str) -> int:
        """Remove all tools registered under source_name. Returns count removed."""
        log.tool.debug(
            "[tools] registry.unregister_by_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        for name in names:
            self._tools.pop(name, None)
        log.tool.debug(
            "[tools] registry.unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def to_provider_schema(self, protocol: str) -> list[dict[str, object]]:
        """Emit tool schemas in the format expected by the given provider protocol."""
        tools = self.all()
        if protocol == "anthropic":
            return [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        return [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }
            for t in tools
        ]

    @classmethod
    def with_defaults(cls) -> ToolRegistry:
        """Bootstrap the registry with the foundation tools + browser family."""
        from stackowl.tools.browser.browse import BrowserBrowseTool
        from stackowl.tools.browser.tools import ATOMIC_BROWSER_TOOLS
        from stackowl.tools.io.read_file import ReadFileTool
        from stackowl.tools.io.web_fetch import WebFetchTool
        from stackowl.tools.io.write_file import WriteFileTool
        from stackowl.tools.system.shell import ShellTool

        registry = cls()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(ShellTool())
        registry.register(WebFetchTool())
        for tool_cls in ATOMIC_BROWSER_TOOLS:
            registry.register(tool_cls())
        registry.register(BrowserBrowseTool())
        return registry
