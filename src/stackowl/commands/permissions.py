"""PermissionsCommand — ``/permissions`` slash command (read-only view)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.config.settings import Settings
    from stackowl.integrations.registry import IntegrationRegistry
    from stackowl.pipeline.state import PipelineState
    from stackowl.plugins.registry import PluginRegistry


class PermissionsCommand(SlashCommand):
    """``/permissions`` slash command — display current autonomy level, owl tool allowlists,
    connected integrations and active plugins as a read-only text block.
    """

    def __init__(
        self,
        settings: Settings | None,
        integration_registry: IntegrationRegistry | None,
        plugin_registry: PluginRegistry | None,
    ) -> None:
        # 1. ENTRY
        log.gateway.debug("[commands] permissions.init: entry")
        self._settings = settings
        self._integration_registry = integration_registry
        self._plugin_registry = plugin_registry
        # 4. EXIT
        log.gateway.debug("[commands] permissions.init: exit")

    @property
    def command(self) -> str:
        return "permissions"

    @property
    def description(self) -> str:
        return "Show current autonomy level, owl tool allowlists, integrations and plugins"

    async def handle(self, args: str, state: PipelineState) -> str:
        """Execute /permissions — return a read-only permissions summary."""
        # 1. ENTRY
        log.gateway.debug(
            "[commands] permissions.handle: entry",
            extra={"_fields": {"session": state.session_id}},
        )
        try:
            # 2. DECISION — gather all data sources
            log.gateway.debug("[commands] permissions.handle: decision — assembling permissions view")

            lines: list[str] = ["=== Permissions ===", ""]

            # Autonomy level
            autonomy = getattr(self._settings, "autonomy_level", "medium")
            lines.append(f"Autonomy level: {autonomy}")
            lines.append("")

            # Owl tool allowlists
            from stackowl.mcp.tool_exposure import DEFAULT_MCP_BROWSER_DENYLIST

            def _decorate(name: str) -> str:
                # Mark consequential browser tools with '!' so users see at a glance
                # which entries warrant scrutiny.
                return f"!{name}" if name in DEFAULT_MCP_BROWSER_DENYLIST else name

            owls = getattr(self._settings, "owls", [])
            if owls:
                lines.append("Owl tool allowlists:")
                lines.append("  (consequential tools prefixed with '!')")
                for owl in owls:
                    tools: list[str] = getattr(owl, "tools", [])
                    tool_str = ", ".join(_decorate(t) for t in tools) if tools else "(none)"
                    lines.append(f"  {owl.name}: {tool_str}")
            else:
                lines.append("Owl tool allowlists: (no owls configured)")
            lines.append("")

            # 3. STEP — integrations
            log.gateway.debug("[commands] permissions.handle: step — listing integrations")
            integrations = self._integration_registry.list_all() if self._integration_registry is not None else []
            if integrations:
                lines.append("Connected integrations:")
                for adapter in integrations:
                    lines.append(f"  {adapter.service_name}")
            else:
                lines.append("Connected integrations: (none)")
            lines.append("")

            # Active plugins
            log.gateway.debug("[commands] permissions.handle: step — listing plugins")
            try:
                plugins = self._plugin_registry.list() if self._plugin_registry is not None else []
            except Exception as exc:
                log.gateway.warning(
                    "[commands] permissions.handle: plugin list failed",
                    exc_info=exc,
                )
                plugins = []
            if plugins:
                lines.append("Active plugins:")
                for plugin in plugins:
                    lines.append(f"  {plugin.name} v{plugin.version}")
            else:
                lines.append("Active plugins: (none)")

            result = "\n".join(lines)

        except Exception as exc:
            log.gateway.error("[commands] permissions.handle: failed", exc_info=exc)
            return f"Error reading permissions: {exc}"

        # 4. EXIT
        log.gateway.debug(
            "[commands] permissions.handle: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result
