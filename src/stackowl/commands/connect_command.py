"""ConnectCommand / DisconnectCommand — ``/connect`` and ``/disconnect`` slash commands (Story 11.2/11.3/11.4)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, render_usage
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.integrations.registry import IntegrationRegistry
    from stackowl.pipeline.state import PipelineState

_CONNECT_META = CommandMeta(
    grammar="flag",
    group="Integrations",
    args=(Arg("service", required=False, summary="integration service name"),),
)
_DISCONNECT_META = CommandMeta(
    grammar="flag",
    group="Integrations",
    args=(Arg("service", required=False, summary="integration service name"),),
)


class ConnectCommand(SlashCommand):
    """``/connect`` slash command — manage integration connections.

    With no arguments, lists all registered integrations and their status.
    With a service name, initiates the OAuth flow for that service.
    """

    def __init__(self, integration_registry: IntegrationRegistry | None) -> None:
        log.gateway.debug("connect_command.__init__: entry")
        self._registry = integration_registry
        log.gateway.debug("connect_command.__init__: exit")

    @property
    def command(self) -> str:
        return "connect"

    @property
    def description(self) -> str:
        return "Connect an external integration (gmail, google_calendar, ...)"

    @property
    def meta(self) -> CommandMeta:
        return _CONNECT_META

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "connect_command.handle: entry",
            extra={"_fields": {"args": args[:40], "session": state.session_id}},
        )
        if self._registry is None:
            log.gateway.warning("connect_command.handle: integration_registry not configured")
            return "✗ /connect: not configured (integration registry unavailable)"
        service = args.strip()
        if not service:
            result = await self._handle_list()
        else:
            result = await self._handle_connect(service)
        log.gateway.debug(
            "connect_command.handle: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result

    async def _handle_list(self) -> str:
        """Return a formatted list of all registered integrations with their status."""
        assert self._registry is not None  # guarded by handle()
        log.gateway.debug("connect_command._handle_list: entry")
        adapters = self._registry.list_all()
        if not adapters:
            log.gateway.debug("connect_command._handle_list: exit — no adapters registered")
            return "No integrations registered. Install an integration plugin first."
        lines = ["Available integrations:\n"]
        for adapter in adapters:
            try:
                connected = await adapter.is_connected()
                status = "connected" if connected else "not connected"
            except Exception as exc:
                log.gateway.warning(
                    "connect_command._handle_list: adapter status check failed",
                    exc_info=exc,
                    extra={"_fields": {"service": adapter.service_name}},
                )
                status = "unknown"
            lines.append(f"  {adapter.service_name}: {status}")
        log.gateway.debug(
            "connect_command._handle_list: exit",
            extra={"_fields": {"count": len(adapters)}},
        )
        return "\n".join(lines)

    async def _handle_connect(self, service: str) -> str:
        """Initiate the OAuth flow for the named service."""
        assert self._registry is not None  # guarded by handle()
        from stackowl.exceptions import IntegrationNotFoundError

        log.gateway.debug(
            "connect_command._handle_connect: entry",
            extra={"_fields": {"service": service}},
        )
        try:
            adapter = self._registry.get(service)
        except IntegrationNotFoundError:
            log.gateway.debug(
                "connect_command._handle_connect: decision — service not found",
                extra={"_fields": {"service": service}},
            )
            return (
                f"Unknown integration: {service!r}. "
                "Run /connect to see available integrations."
            )
        try:
            await adapter.connect()
            log.gateway.info(
                "connect_command._handle_connect: step — connect completed",
                extra={"_fields": {"service": service}},
            )
            log.gateway.debug("connect_command._handle_connect: exit — success")
            return f"{service} connected."
        except Exception as exc:
            log.gateway.error(
                "connect_command._handle_connect: connect failed",
                exc_info=exc,
                extra={"_fields": {"service": service}},
            )
            return f"Failed to connect {service}: {exc}"


class DisconnectCommand(SlashCommand):
    """``/disconnect`` slash command — remove an integration connection."""

    def __init__(self, integration_registry: IntegrationRegistry | None) -> None:
        log.gateway.debug("disconnect_command.__init__: entry")
        self._registry = integration_registry
        log.gateway.debug("disconnect_command.__init__: exit")

    @property
    def command(self) -> str:
        return "disconnect"

    @property
    def description(self) -> str:
        return "Disconnect an external integration and remove stored credentials"

    @property
    def meta(self) -> CommandMeta:
        return _DISCONNECT_META

    async def handle(self, args: str, state: PipelineState) -> str:
        # 1. ENTRY
        log.gateway.debug(
            "disconnect_command.handle: entry",
            extra={"_fields": {"args": args[:40]}},
        )
        if self._registry is None:
            log.gateway.warning("disconnect_command.handle: integration_registry not configured")
            return "✗ /disconnect: not configured (integration registry unavailable)"
        service = args.strip()
        if not service:
            log.gateway.debug("disconnect_command.handle: exit — no args, returning usage")
            return render_usage("disconnect", _DISCONNECT_META)

        # 2. DECISION — look up the adapter
        from stackowl.exceptions import IntegrationNotFoundError

        try:
            adapter = self._registry.get(service)
        except IntegrationNotFoundError:
            log.gateway.debug(
                "disconnect_command.handle: decision — service not found",
                extra={"_fields": {"service": service}},
            )
            return (
                f"Unknown integration: {service!r}. "
                "Run /connect to see available integrations."
            )

        # 3. STEP — disconnect via public protocol, then delete credentials via
        #    public delete_credentials() — never reach into private _oauth directly
        try:
            if hasattr(adapter, "disconnect"):
                await adapter.disconnect()
            creds_removed = await adapter.delete_credentials()
            log.gateway.info(
                "disconnect_command.handle: step — disconnected",
                extra={"_fields": {"service": service, "creds_removed": creds_removed}},
            )
            if creds_removed:
                result = f"✓ {service} disconnected and credentials removed."
            else:
                result = f"✓ {service} disconnected (no stored credentials to remove)."
        except Exception as exc:
            log.gateway.error(
                "disconnect_command.handle: disconnect failed",
                exc_info=exc,
                extra={"_fields": {"service": service}},
            )
            result = f"✗ Failed to disconnect {service}: {exc}"

        # 4. EXIT
        log.gateway.debug(
            "disconnect_command.handle: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result
