"""WebhookCommand — ``/webhook`` slash command (Story 7.5).

Subcommands:

* ``register <source>`` — print the YAML stanza the user needs to add
* ``list``              — show configured sources + last receipt timestamp
* ``disable <source>``  — print the YAML disable stanza + audit-log the request

The command intentionally never *writes* config or secrets at runtime: editing
``stackowl.yaml`` and managing secrets are user operations.  It only emits
instructions and records an audit-log entry for disables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.infra.observability import log
from stackowl.scheduler.scheduler_helpers import write_audit

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.pipeline.state import PipelineState


_USAGE = (
    "Usage:\n"
    "  /webhook register <source>   — print YAML stanza to register a source\n"
    "  /webhook list                — show configured sources + recent receipts\n"
    "  /webhook disable <source>    — print YAML disable stanza and audit-log"
)
_LIST_SQL = (
    "SELECT source, MAX(received_at) AS last_received, COUNT(*) AS event_count "
    "FROM webhook_events_log GROUP BY source"
)


class WebhookCommand(SlashCommand):
    """``/webhook`` — manage webhook sources via YAML hints + audit-log writes."""

    def __init__(self, db: DbPool | None = None, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings

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

    async def handle(self, args: str, state: PipelineState) -> str:
        if self._db is None or self._settings is None:
            return "✗ /webhook: not configured"
        # 1. ENTRY
        log.webhook.debug(
            "[webhook] command.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        parts = args.strip().split()
        if not parts:
            return _USAGE

        sub = parts[0]
        rest = parts[1:]

        if sub == "register":
            if not rest:
                return "webhook register: missing <source>\n\n" + _USAGE
            return await self._register(rest[0], state)
        if sub == "list":
            return await self._list(state)
        if sub == "disable":
            if not rest:
                return "webhook disable: missing <source>\n\n" + _USAGE
            return await self._disable(rest[0], state)

        log.webhook.debug(
            "[webhook] command.handle: unknown subcommand",
            extra={"_fields": {"sub": sub}},
        )
        return f"webhook: unknown subcommand {sub!r}\n\n{_USAGE}"

    # ------------------------------------------------------------------ subs

    async def _register(self, source: str, state: PipelineState) -> str:
        log.webhook.info(
            "[webhook] command.register: returning config instructions",
            extra={"_fields": {"source": source}},
        )
        env_var = f"WEBHOOK_{source.upper()}_SECRET"
        return (
            f"To register webhook '{source}', add to stackowl.yaml:\n"
            "  webhook:\n"
            "    enabled: true\n"
            "    sources:\n"
            f"      {source}:\n"
            f"        secret: env:{env_var}\n"
            f"        enabled: true\n"
            "\n"
            f"Then export the shared secret: export {env_var}=...\n"
            "Restart the supervisor to bind the new source."
        )

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
