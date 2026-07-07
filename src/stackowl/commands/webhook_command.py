"""WebhookCommand — ``/webhook`` slash command (Story 7.5).

Subcommands:

* ``register <source> [timestamp_header=<H>] [delivery_id_header=<H>]
  [secret=<RAW>] [replay_tolerance_s=<N>]`` — really register a source: writes
  ``stackowl.yaml`` and persists the secret via the shared secret writer.
* ``list``              — show configured sources + last receipt timestamp
* ``disable <source>``  — print the YAML disable stanza + audit-log the request

SECURITY: a supplied secret is NEVER written in plaintext and NEVER logged or
echoed. It is persisted via :func:`store_secret` (OS keyring → mode-0600 file
fallback); only the resulting SecretResolver *ref* (``keychain:…`` /
``file:…``) is stored in the YAML ``secret`` field, mirroring
``/provider add`` (see ``provider_command.py``).
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from stackowl.commands.base import SlashCommand
from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml
from stackowl.commands.metadata import Arg, CommandMeta, SubCommand, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.config.secret_writer import store_secret
from stackowl.config.settings import Settings
from stackowl.infra.observability import log
from stackowl.scheduler.scheduler_helpers import write_audit

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.pipeline.state import PipelineState


_WEBHOOK_META = CommandMeta(
    grammar="verb",
    group="Integrations",
    subcommands=(
        SubCommand(
            name="register",
            summary="Register a new webhook source",
            description=(
                "You add a webhook source. At least one anti-replay mechanism "
                "(timestamp_header or delivery_id_header) is required — the "
                "sending service's docs will name its header. A shared secret is "
                "auto-generated and shown once if you don't supply one."
            ),
            args=(
                Arg(name="source", summary="webhook source name"),
                Arg(
                    name="timestamp_header=<H>",
                    required=False,
                    summary="signed-timestamp header name",
                ),
                Arg(
                    name="delivery_id_header=<H>",
                    required=False,
                    summary="delivery-id header name",
                ),
                Arg(name="secret=<RAW>", required=False, summary="shared secret (auto-generated if omitted)"),
                Arg(
                    name="replay_tolerance_s=<N>",
                    required=False,
                    summary="max signed-timestamp age, seconds (default 300)",
                ),
            ),
        ),
        SubCommand(
            name="list",
            summary="Show configured sources and recent receipts",
        ),
        SubCommand(
            name="disable",
            summary="Print the disable stanza and audit-log the request",
            args=(Arg(name="source", summary="webhook source name"),),
        ),
    ),
)
_LIST_SQL = (
    "SELECT source, MAX(received_at) AS last_received, COUNT(*) AS event_count "
    "FROM webhook_events_log GROUP BY source"
)


class WebhookCommand(SlashCommand):
    """``/webhook`` — manage webhook sources via YAML hints + audit-log writes."""

    def __init__(
        self,
        db: DbPool | None = None,
        settings: Settings | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._bus = event_bus

    @property
    def command(self) -> str:
        return "webhook"

    # Spec alias — `name` is the canonical attribute used by Story 7.5's
    # description.  Kept identical to ``command`` so any caller can use either.
    @property
    def name(self) -> str:
        return self.command

    @property
    def description(self) -> str:
        return "Show webhook source config instructions and audit disable requests"

    @property
    def meta(self) -> CommandMeta:
        return _WEBHOOK_META

    async def handle(self, args: str, state: PipelineState) -> str:
        if self._db is None or self._settings is None:
            return "✗ /webhook: not configured"
        # 1. ENTRY
        log.webhook.debug(
            "[webhook] command.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        usage = render_usage("webhook", _WEBHOOK_META)
        parts = args.strip().split()
        if not parts:
            return usage

        sub = parts[0]
        rest = parts[1:]

        if sub == "register":
            if not rest:
                return "webhook register: missing <source>\n\n" + usage
            return await self._register(rest[0], rest[1:], state)
        if sub == "list":
            return await self._list(state)
        if sub == "disable":
            if not rest:
                return "webhook disable: missing <source>\n\n" + usage
            return await self._disable(rest[0], state)

        log.webhook.debug(
            "[webhook] command.handle: unknown subcommand",
            extra={"_fields": {"sub": sub}},
        )
        return f"webhook: unknown subcommand {sub!r}\n\n{usage}"

    # ------------------------------------------------------------------ subs

    async def _register(self, source: str, extra_args: list[str], state: PipelineState) -> str:
        log.webhook.info(
            "[webhook] command.register: entry",
            extra={"_fields": {"source": source, "extra_args_count": len(extra_args)}},
        )
        timestamp_header: str | None = None
        delivery_id_header: str | None = None
        raw_secret: str | None = None
        replay_tolerance_s = 300
        for token in extra_args:
            if token.startswith("timestamp_header="):
                timestamp_header = token[len("timestamp_header="):]
            elif token.startswith("delivery_id_header="):
                delivery_id_header = token[len("delivery_id_header="):]
            elif token.startswith("secret="):
                raw_secret = token[len("secret="):]
            elif token.startswith("replay_tolerance_s="):
                try:
                    replay_tolerance_s = int(token[len("replay_tolerance_s="):])
                except ValueError:
                    return f"✗ replay_tolerance_s must be an integer, got {token!r}"
            else:
                return f"✗ Unrecognized argument: {token!r}"

        if not timestamp_header and not delivery_id_header:
            return (
                "✗ webhook register requires an anti-replay mechanism — set "
                "timestamp_header=<H> (preferred, signed-timestamp window) or "
                "delivery_id_header=<H> (sender delivery-id). Check the sending "
                "service's docs for its header name — StackOwl cannot guess it."
            )

        path = config_path()
        data = load_yaml(path)
        webhook_cfg = data.setdefault("webhook", {})
        sources = webhook_cfg.setdefault("sources", {})
        was_already_enabled = bool(webhook_cfg.get("enabled", False)) and bool(sources)

        secret_shown_once: str | None = None
        if raw_secret is None:
            raw_secret = secrets.token_urlsafe(32)
            secret_shown_once = raw_secret
        _description, secret_ref = store_secret(f"stackowl-webhook-{source}", raw_secret)

        source_entry: dict[str, Any] = {
            "enabled": True,
            "secret": secret_ref,
            "replay_tolerance_s": replay_tolerance_s,
        }
        if timestamp_header:
            source_entry["timestamp_header"] = timestamp_header
        if delivery_id_header:
            source_entry["delivery_id_header"] = delivery_id_header

        sources[source] = source_entry
        webhook_cfg["enabled"] = True
        save_yaml(path, data)

        # F-81-style verified-persist re-read before claiming success.
        reloaded = load_yaml(path)
        if source not in reloaded.get("webhook", {}).get("sources", {}):
            log.webhook.error(
                "[webhook] command.register: write did not persist",
                extra={"_fields": {"source": source}},
            )
            return f"✗ Webhook '{source}' was not saved — check file permissions/disk."

        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.webhook.error(
                    "[webhook] command.register: immediate reload failed",
                    exc_info=exc,
                    extra={"_fields": {"source": source}},
                )

        log.webhook.info(
            "[webhook] command.register: exit — registered",
            extra={"_fields": {"source": source, "first_source": not was_already_enabled}},
        )
        lines = [f"✓ Webhook '{source}' registered."]
        if was_already_enabled:
            lines.append("Live now — no restart needed.")
        else:
            lines.append(
                "This is the first webhook source — restart is required to "
                "start the listener."
            )
        if secret_shown_once:
            lines.append(
                f"Shared secret (save now, shown once): {secret_shown_once}"
            )
            lines.append("Give this to the sending service to sign requests.")
        return "\n".join(lines)

    async def _list(self, state: PipelineState) -> str:
        assert self._db is not None and self._settings is not None  # narrowed by handle() guard
        log.webhook.debug("[webhook] command.list: entry")
        configured = sorted(self._settings.webhook.sources.keys())
        try:
            rows = await self._db.fetch_all(_LIST_SQL, ())
        except Exception as exc:  # B5 — never silent
            log.webhook.warning(
                "[webhook] command.list: query failed",
                exc_info=exc,
            )
            rows = []
        last_by_source: dict[str, str] = {
            str(r["source"]): str(r["last_received"]) for r in rows
        }
        counts_by_source: dict[str, int] = {
            str(r["source"]): int(r["event_count"]) for r in rows
        }
        if not configured:
            return "webhook: no sources configured.  Add some via /webhook register."
        lines = [f"webhook: {len(configured)} source(s) configured:"]
        for src in configured:
            cfg = self._settings.webhook.sources[src]
            state_label = "enabled" if cfg.enabled else "disabled"
            last = last_by_source.get(src, "never")
            count = counts_by_source.get(src, 0)
            lines.append(
                f"  - {src} [{state_label}] — events:{count}, last:{last}"
            )
        log.webhook.debug(
            "[webhook] command.list: exit",
            extra={"_fields": {"configured": len(configured)}},
        )
        return "\n".join(lines)

    async def _disable(self, source: str, state: PipelineState) -> str:
        assert self._db is not None  # narrowed by handle() guard
        log.webhook.info(
            "[webhook] command.disable: instructions + audit-log",
            extra={"_fields": {"source": source}},
        )
        await write_audit(
            self._db,
            event_type="webhook_disabled",
            target=source,
            actor=state.session_id or "user",
            details={"reason": "user_requested"},
        )
        return (
            f"To disable webhook '{source}', edit stackowl.yaml:\n"
            "  webhook:\n"
            "    sources:\n"
            f"      {source}:\n"
            "        enabled: false\n"
            "\n"
            "Then restart the supervisor."
        )

    @classmethod
    def create_and_register(cls, db: DbPool, settings: Settings) -> WebhookCommand:
        cmd = cls(db=db, settings=settings)
        CommandRegistry.instance().register(cmd)
        return cmd
