"""PluginsCommand — ``/plugins`` slash command for plugin management."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, SubCommand, render_usage
from stackowl.commands.response import Action, CommandResponse
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.pipeline.state import PipelineState
    from stackowl.plugins.registry import PluginRegistry


_PLUGINS_META = CommandMeta(
    grammar="verb",
    group="Plugins",
    subcommands=(
        SubCommand(
            name="list",
            summary="List every installed plugin with version and type",
        ),
        SubCommand(
            name="info",
            summary="Show full metadata for one plugin",
            args=(Arg(name="name", summary="installed plugin name"),),
        ),
        SubCommand(
            name="enable",
            summary="Turn a plugin on",
            args=(Arg(name="name", summary="installed plugin name"),),
        ),
        SubCommand(
            name="disable",
            summary="Turn a plugin off",
            args=(Arg(name="name", summary="installed plugin name"),),
        ),
    ),
)


class PluginsCommand(SlashCommand):
    """``/plugins`` slash command — manage installed plugins."""

    def __init__(self, plugin_registry: PluginRegistry | None) -> None:
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

    @property
    def meta(self) -> CommandMeta:
        return _PLUGINS_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command.handle: entry",
            extra={"_fields": {"args": args[:80]}},
        )
        if self._registry is None:
            log.gateway.warning("plugins_command.handle: registry not configured")
            return "✗ /plugins: not configured (plugin registry unavailable)"
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
            elif sub == "menu" and arg:
                result = await self._handle_menu(arg.strip())
            else:
                result = render_usage("plugins", _PLUGINS_META)
        except Exception as exc:
            log.gateway.error(
                "plugins_command.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"Error running /plugins {sub}: {exc}"

        # 4. EXIT
        out_text = result.text if isinstance(result, CommandResponse) else result
        log.gateway.debug(
            "plugins_command.handle: exit",
            extra={"_fields": {"sub": sub, "len": len(out_text)}},
        )
        return result

    def _handle_list(self) -> str | CommandResponse:
        assert self._registry is not None  # guarded by handle()
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
        actions = []
        for p in plugins:
            lines.append(
                f"  {p.name}  v{p.version}  [{p.type}]  — {p.description[:60]}"
            )
            actions.append(
                Action(label=p.name, command=f"/plugins menu {p.name}", destructive=False)
            )
        result = "\n".join(lines)
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_list: exit",
            extra={"_fields": {"count": len(plugins)}},
        )
        return CommandResponse(text=result, actions=tuple(actions))

    async def _handle_info(self, name: str) -> str:
        assert self._registry is not None  # guarded by handle()
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

    async def _handle_menu(self, name: str) -> str | CommandResponse:
        assert self._registry is not None  # guarded by handle()
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command._handle_menu: entry",
            extra={"_fields": {"name": name}},
        )
        # ponytail: registry.list() only returns enabled=1 rows (PluginRegistry.list
        # filters WHERE enabled = 1), so any plugin reachable via a list-row tap is
        # always currently enabled — toggle is always "Disable" here. Same gap
        # already exists in /plugins info (a disabled plugin is invisible to both).
        # Upgrade path: add PluginRegistry.get(name) returning disabled rows too.
        plugins = self._registry.list()
        found = next((p for p in plugins if p.name == name), None)
        if found is None:
            log.gateway.debug(
                "plugins_command._handle_menu: not found",
                extra={"_fields": {"name": name}},
            )
            return (
                f"Plugin '{name}' not found. "
                "Run /plugins list to see installed plugins."
            )
        text = (
            f"{found.name}  v{found.version}  [{found.type}]  — enabled\n"
            f"{found.description[:120]}"
        )
        actions = (
            Action(label="Disable", command=f"/plugins disable {found.name}", destructive=False),
            Action(label="Info", command=f"/plugins info {found.name}", destructive=False),
        )
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_menu: exit",
            extra={"_fields": {"name": found.name}},
        )
        return CommandResponse(text=text, actions=actions)

    async def _handle_enable(self, name: str) -> str:
        assert self._registry is not None  # guarded by handle()
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command._handle_enable: entry",
            extra={"_fields": {"name": name}},
        )
        # 2. DECISION — existence check before claiming success
        if not self._registry.exists(name):
            log.gateway.debug(
                "plugins_command._handle_enable: not found",
                extra={"_fields": {"name": name}},
            )
            return (
                f"Plugin '{name}' not found. "
                "Run /plugins list to see installed plugins."
            )
        await self._registry.set_enabled(name, enabled=True)
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_enable: exit",
            extra={"_fields": {"name": name}},
        )
        return f"Plugin '{name}' enabled."

    async def _handle_disable(self, name: str) -> str:
        assert self._registry is not None  # guarded by handle()
        # 1. ENTRY
        log.gateway.debug(
            "plugins_command._handle_disable: entry",
            extra={"_fields": {"name": name}},
        )
        # 2. DECISION — existence check before claiming success
        if not self._registry.exists(name):
            log.gateway.debug(
                "plugins_command._handle_disable: not found",
                extra={"_fields": {"name": name}},
            )
            return (
                f"Plugin '{name}' not found. "
                "Run /plugins list to see installed plugins."
            )
        await self._registry.set_enabled(name, enabled=False)
        # 4. EXIT
        log.gateway.debug(
            "plugins_command._handle_disable: exit",
            extra={"_fields": {"name": name}},
        )
        return f"Plugin '{name}' disabled."
