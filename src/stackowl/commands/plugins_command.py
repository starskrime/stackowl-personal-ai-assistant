"""PluginsCommand — ``/plugins`` slash command for plugin management."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.state import PipelineState
    from stackowl.plugins.registry import PluginRegistry


_USAGE = (
    "Usage:\n"
    "  /plugins list\n"
    "  /plugins info <name>\n"
    "  /plugins enable <name>\n"
    "  /plugins disable <name>"
)


class PluginsCommand(SlashCommand):
    """``/plugins`` slash command — manage installed plugins."""

    def __init__(self, plugin_registry: PluginRegistry) -> None:
        # 1. ENTRY
        log.gateway.debug("plugins_command.__init__: entry")
        self._registry = plugin_registry
        # 4. EXIT
        log.gateway.debug("plugins_command.__init__: exit")

    @property
    def command(self) -> str:
        return "plugins"

    @property
    def description(self) -> str:
        return "List and manage installed plugins"

    async def handle(self, args: str, state: PipelineState) -> str:
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command.handle: entry",
            extra={"_fields": {"args": args[:80]}},
        )
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        arg = parts[1] if len(parts) > 1 else ""

        # 2. DECISION
        log.gateway.debug(
            "plugins_command.handle: decision",
            extra={"_fields": {"sub": sub}},
        )

        try:
            if sub == "list":
                result = self._handle_list()
            elif sub == "info" and arg:
                result = await self._handle_info(arg.strip())
            elif sub == "enable" and arg:
                result = await self._handle_enable(arg.strip())
            elif sub == "disable" and arg:
                result = await self._handle_disable(arg.strip())
            else:
                result = _USAGE
        except Exception as exc:
            log.gateway.error(
                "plugins_command.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"Error running /plugins {sub}: {exc}"

        # 4. EXIT
        log.gateway.debug(
            "plugins_command.handle: exit",
            extra={"_fields": {"sub": sub, "len": len(result)}},
        )
        return result

    def _handle_list(self) -> str:
        # 1. ENTRY
        log.gateway.debug("plugins_command._handle_list: entry")
        plugins = self._registry.list()
        # 2. DECISION
        if not plugins:
            log.gateway.debug(
                "plugins_command._handle_list: decision — no plugins installed"
            )
            return "No plugins installed."
        lines = ["Installed plugins:\n"]
        for p in plugins:
            lines.append(
                f"  {p.name}  v{p.version}  [{p.type}]  — {p.description[:60]}"
            )
        result = "\n".join(lines)
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_list: exit",
            extra={"_fields": {"count": len(plugins)}},
        )
        return result

    async def _handle_info(self, name: str) -> str:
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command._handle_info: entry",
            extra={"_fields": {"name": name}},
        )
        plugins = self._registry.list()
        # 2. DECISION
        found = next((p for p in plugins if p.name == name), None)
        if found is None:
            log.gateway.debug(
                "plugins_command._handle_info: not found",
                extra={"_fields": {"name": name}},
            )
            return (
                f"Plugin '{name}' not found. "
                "Run /plugins list to see installed plugins."
            )
        # 3. STEP — format details
        caps = ", ".join(found.capabilities) if found.capabilities else "(none)"
        schema = (
            json.dumps(found.config_schema, indent=2)
            if found.config_schema
            else "(none)"
        )
        result = (
            f"Plugin: {found.name}\n"
            f"Version: {found.version}\n"
            f"Type: {found.type}\n"
            f"Entry point: {found.entry_point}\n"
            f"Capabilities: {caps}\n"
            f"Description: {found.description}\n"
            f"Author: {found.author or '(unknown)'}\n"
            f"License: {found.license or '(unspecified)'}\n"
            f"Config schema: {schema}"
        )
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_info: exit",
            extra={"_fields": {"name": name}},
        )
        return result

    async def _handle_enable(self, name: str) -> str:
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command._handle_enable: entry",
            extra={"_fields": {"name": name}},
        )
        await self._registry.set_enabled(name, enabled=True)
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_enable: exit",
            extra={"_fields": {"name": name}},
        )
        return f"Plugin '{name}' enabled."

    async def _handle_disable(self, name: str) -> str:
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command._handle_disable: entry",
            extra={"_fields": {"name": name}},
        )
        await self._registry.set_enabled(name, enabled=False)
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_disable: exit",
            extra={"_fields": {"name": name}},
        )
        return f"Plugin '{name}' disabled."
