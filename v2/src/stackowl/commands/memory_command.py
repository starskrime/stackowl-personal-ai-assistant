"""MemoryCommand — ``/memory`` slash command for memory management.

Subcommands:

* ``/memory stats``                       — counts + storage bytes
* ``/memory search <query>``              — recall against committed facts
* ``/memory delete <id> [YES]``           — delete a fact (requires YES)
* ``/memory budget``                      — show per-user storage vs ceiling
* ``/memory reindex``                     — push every committed fact to LanceDB
* ``/memory remember <text>``             — explicitly stage + promote a fact
* ``/memory forget <fact_id_prefix>``     — delete a fact by id prefix
* ``/memory export [--format json|csv] [--output <path>]`` — dump committed facts
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.memory_helpers import (
    collect_stats,
    do_export,
    fetch_all_committed_for_reindex,
    format_budget,
    format_search_hits,
    format_stats,
    parse_export_args,
    remember_fact,
)
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.staged_helpers import find_staged_by_id
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.pipeline.state import PipelineState


_USAGE = (
    "Usage:\n"
    "  /memory stats\n"
    "  /memory search <query>\n"
    "  /memory delete <fact_id> [YES]\n"
    "  /memory budget\n"
    "  /memory reindex\n"
    "  /memory remember <text>\n"
    "  /memory forget <fact_id_prefix> [YES]\n"
    "  /memory export [--format json|csv] [--output <path>]"
)
_CONFIRMATION = "YES"


class MemoryCommand(SlashCommand):
    """``/memory`` slash command — see module docstring."""

    def __init__(
        self,
        bridge: MemoryBridge,
        settings: Settings,
        db: DbPool,
        event_bus: EventBus,
        lancedb: LanceDBAdapter | None = None,
        promoter: FactPromoter | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[commands] memory.init: entry",
            extra={
                "_fields": {
                    "has_lancedb": lancedb is not None,
                    "has_promoter": promoter is not None,
                }
            },
        )
        self._bridge = bridge
        self._settings = settings
        self._db = db
        self._bus = event_bus
        self._lancedb = lancedb
        self._promoter = promoter
        # 4. EXIT
        log.memory.debug("[commands] memory.init: exit")

    @property
    def command(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Memory management commands (stats, search, delete, budget, reindex)."

    async def handle(self, args: str, state: PipelineState) -> str:
        # 1. ENTRY
        log.memory.debug(
            "[commands] memory.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        stripped = args.strip()
        if not stripped:
            return _USAGE
        parts = stripped.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        try:
            # 2. DECISION — dispatch by subcommand
            if sub == "stats":
                result = await self._stats()
            elif sub == "search":
                result = await self._search(rest.strip())
            elif sub == "delete":
                result = await self._delete(rest.strip())
            elif sub == "budget":
                result = await self._budget()
            elif sub == "reindex":
                result = await self._reindex()
            elif sub == "remember":
                result = await self._remember(rest)
            elif sub == "forget":
                result = await self._forget(rest.strip())
            elif sub == "export":
                result = await self._export(rest)
            else:
                log.memory.debug(
                    "[commands] memory.handle: decision — unknown subcommand",
                    extra={"_fields": {"sub": sub[:40]}},
                )
                return _USAGE
        except Exception as exc:
            # B5
            log.memory.error(
                "[commands] memory.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /memory {sub}: {exc}"
        # 4. EXIT
        log.memory.debug(
            "[commands] memory.handle: exit",
            extra={"_fields": {"sub": sub, "out_len": len(result)}},
        )
        return result

    # ----- subcommands ---------------------------------------------------------

    async def _stats(self) -> str:
        log.memory.debug("[commands] memory.stats: entry")
        stats = await collect_stats(self._db)
        out = format_stats(stats)
        log.memory.debug(
            "[commands] memory.stats: exit",
            extra={"_fields": dict(stats)},
        )
        return out

    async def _search(self, query: str) -> str:
        log.memory.debug(
            "[commands] memory.search: entry",
            extra={"_fields": {"query_len": len(query)}},
        )
        if not query:
            return "Usage: /memory search <query>"
        hits = await self._bridge.recall(query, limit=5)
        out = format_search_hits(hits)
        log.memory.debug(
            "[commands] memory.search: exit",
            extra={"_fields": {"n_hits": len(hits)}},
        )
        return out

    async def _delete(self, args: str) -> str:
        log.memory.debug(
            "[commands] memory.delete: entry",
            extra={"_fields": {"args_len": len(args)}},
        )
        if not args:
            return "Usage: /memory delete <fact_id> [YES]"
        parts = args.split(maxsplit=1)
        fact_id = parts[0]
        confirmation = parts[1].strip() if len(parts) > 1 else ""
        if confirmation != _CONFIRMATION:
            log.memory.debug("[commands] memory.delete: decision — missing YES")
            return (
                f"Confirm deletion of '{fact_id}'.\n"
                f"Type '/memory delete {fact_id} YES' to proceed."
            )
        await self._bridge.delete(fact_id)
        log.memory.info(
            "[commands] memory.delete: exit",
            extra={"_fields": {"fact_id": fact_id}},
        )
        return f"✓ Deleted {fact_id}"

    async def _budget(self) -> str:
        log.memory.debug("[commands] memory.budget: entry")
        ceiling = self._settings.memory.per_user_ceiling_bytes
        rows = await self._db.fetch_all(
            "SELECT COALESCE(SUM(length(content)), 0) AS s FROM committed_facts"
        )
        usage = int(rows[0]["s"]) if rows else 0
        out = format_budget(usage, ceiling)
        log.memory.debug(
            "[commands] memory.budget: exit",
            extra={"_fields": {"usage_bytes": usage, "ceiling_bytes": ceiling}},
        )
        return out

    async def _reindex(self) -> str:
        log.memory.info("[commands] memory.reindex: entry")
        if self._lancedb is None:
            log.memory.warning(
                "[commands] memory.reindex: no lancedb adapter configured"
            )
            return "✗ /memory reindex: LanceDB adapter not configured"
        records = await fetch_all_committed_for_reindex(self._db)
        if not records:
            log.memory.info("[commands] memory.reindex: exit — no records")
            return "No committed facts to reindex (0 written)"
        written = await self._lancedb.reindex(records)
        log.memory.info(
            "[commands] memory.reindex: exit",
            extra={"_fields": {"written": written}},
        )
        return f"✓ Reindexed {written} fact(s) into LanceDB"

    async def _remember(self, text: str) -> str:
        log.memory.debug("[commands] memory.remember: entry", extra={"_fields": {"text_len": len(text)}})
        stripped = text.strip()
        if not stripped:
            return "Usage: /memory remember <text>"
        if self._promoter is None:
            log.memory.warning("[commands] memory.remember: no promoter wired")
            return "✗ /memory remember: promoter not configured"
        fact_id = await remember_fact(self._bridge, self._promoter, stripped)
        log.memory.info("[commands] memory.remember: exit", extra={"_fields": {"fact_id": fact_id}})
        return f"✓ Remembered: {fact_id[:8]}"

    async def _forget(self, args: str) -> str:
        log.memory.debug("[commands] memory.forget: entry", extra={"_fields": {"args_len": len(args)}})
        if not args:
            return "Usage: /memory forget <fact_id_prefix> [YES]"
        parts = args.split(maxsplit=1)
        prefix = parts[0]
        confirmation = parts[1].strip() if len(parts) > 1 else ""
        fact = await find_staged_by_id(self._bridge, prefix)
        if fact is None:
            log.memory.debug("[commands] memory.forget: no match", extra={"_fields": {"prefix": prefix[:16]}})
            return f"✗ /memory forget: no fact matches prefix '{prefix}'"
        if confirmation != _CONFIRMATION:
            return (
                f"Forget fact {fact.fact_id[:8]} ('{fact.content[:40]}...')?\n"
                f"   Type: /memory forget {fact.fact_id} YES to confirm."
            )
        await self._bridge.delete(fact.fact_id)
        log.memory.info("[commands] memory.forget: exit", extra={"_fields": {"fact_id": fact.fact_id}})
        return f"✓ Forgotten: {fact.fact_id}"

    async def _export(self, args: str) -> str:
        log.memory.debug("[commands] memory.export: entry", extra={"_fields": {"args_len": len(args)}})
        fmt, output_path = parse_export_args(args)
        facts = await self._bridge.list_staged(status="committed")
        result = await do_export(facts, fmt, output_path)
        log.memory.info(
            "[commands] memory.export: exit",
            extra={"_fields": {"count": len(facts), "format": fmt, "wrote_file": output_path is not None}},
        )
        return result

    # ----- factory -------------------------------------------------------------

    @classmethod
    def create_and_register(
        cls,
        bridge: MemoryBridge,
        settings: Settings,
        db: DbPool,
        event_bus: EventBus,
        lancedb: LanceDBAdapter | None = None,
        promoter: FactPromoter | None = None,
    ) -> MemoryCommand:
        """Construct a :class:`MemoryCommand` and register it on the singleton."""
        cmd = cls(
            bridge=bridge,
            settings=settings,
            db=db,
            event_bus=event_bus,
            lancedb=lancedb,
            promoter=promoter,
        )
        CommandRegistry.instance().register(cmd)
        return cmd
