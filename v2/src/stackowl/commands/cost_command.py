"""CostCommand — /cost slash command for spending visibility (FR196).

Subcommands:
  /cost            → today's spend (USD) by provider and model.
  /cost privacy    → wipe cost_records after explicit YES confirmation.

The command opens a short-lived :class:`DbPool` so it can run from the
slash-command pipeline without depending on the server's running event loop
context.  Imports of CostTracker/DbPool happen inside :meth:`handle` to avoid
import cycles with the providers subsystem.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import register_command
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

_PRIVACY_CONFIRMATION = "YES"


class CostCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "cost"

    @property
    def description(self) -> str:
        return "Show today's spending or wipe cost history (/cost privacy)."

    async def handle(self, args: str, state: PipelineState) -> str:
        log.engine.debug(
            "[commands] cost.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        raw = args.strip()
        if not raw:
            log.engine.debug("[commands] cost.handle: decision — show summary")
            return await self._summary()
        parts = raw.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if sub == "privacy":
            log.engine.debug(
                "[commands] cost.handle: decision — privacy",
                extra={"_fields": {"confirmation_present": bool(rest)}},
            )
            return await self._privacy(rest)
        log.engine.debug(
            "[commands] cost.handle: decision — unknown subcommand",
            extra={"_fields": {"sub": sub[:40]}},
        )
        return (
            "Usage: /cost                 — show today's spend\n"
            "       /cost privacy YES     — wipe cost_records (irreversible)"
        )

    async def _summary(self) -> str:
        log.engine.debug("[commands] cost.summary: entry")
        from stackowl.db.pool import DbPool
        from stackowl.events.bus import EventBus
        from stackowl.providers.cost_tracker import CostTracker

        db = DbPool()
        try:
            await db.open()
        except Exception as exc:
            log.engine.error("[commands] cost.summary: db open failed", exc_info=exc)
            return "No cost data yet"
        try:
            tracker = CostTracker(db=db, event_bus=EventBus(), daily_limit_usd=None)
            try:
                summary = await tracker.daily_total()
            except Exception as exc:
                log.engine.warning(
                    "[commands] cost.summary: daily_total failed (no table?)",
                    extra={"_fields": {"error": str(exc)}},
                )
                return "No cost data yet"
        finally:
            await db.close()

        if summary.call_count == 0:
            log.engine.debug("[commands] cost.summary: exit — no calls today")
            return f"No spend recorded for {summary.date}"

        lines = [
            f"Spend for {summary.date}: ${summary.total_usd:.4f} ({summary.call_count} calls)",
            "",
            "By provider:",
        ]
        for prov, cost in sorted(summary.by_provider.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {prov:<20} ${cost:.4f}")
        lines.append("")
        lines.append("By model:")
        for model, cost in sorted(summary.by_model.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {model:<30} ${cost:.4f}")
        log.engine.debug(
            "[commands] cost.summary: exit",
            extra={"_fields": {"total_usd": summary.total_usd, "calls": summary.call_count}},
        )
        return "\n".join(lines)

    async def _privacy(self, confirmation: str) -> str:
        log.engine.debug(
            "[commands] cost.privacy: entry",
            extra={"_fields": {"confirmation_len": len(confirmation)}},
        )
        if confirmation != _PRIVACY_CONFIRMATION:
            log.engine.debug("[commands] cost.privacy: decision — missing YES")
            return "This will permanently delete all cost records.\nType '/cost privacy YES' to confirm."
        from stackowl.db.pool import DbPool

        db = DbPool()
        try:
            await db.open()
        except Exception as exc:
            log.engine.error("[commands] cost.privacy: db open failed", exc_info=exc)
            return "✗ Could not open database"
        try:
            try:
                await db.execute("DELETE FROM cost_records")
            except Exception as exc:
                log.engine.warning(
                    "[commands] cost.privacy: delete failed (table missing?)",
                    extra={"_fields": {"error": str(exc)}},
                )
                return "No cost data yet"
        finally:
            await db.close()
        log.engine.info("[commands] cost.privacy: exit — cost_records wiped")
        return "✓ Cost history cleared"


_CMD = register_command(CostCommand())
