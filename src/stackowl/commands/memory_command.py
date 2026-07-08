"""MemoryCommand — ``/memory`` slash command for memory management.

Subcommands:

* ``/memory stats``                       — counts + storage bytes
* ``/memory search <query>``              — recall against committed facts
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
    forget_fact,
    format_budget,
    format_search_hits,
    format_stats,
    parse_export_args,
    remember_fact,
)
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse
from stackowl.commands.staged_helpers import find_staged_by_id, format_review
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.fact_promoter import FactPromoter
    from stackowl.memory.lancedb_adapter import LanceDBAdapter
    from stackowl.pipeline.state import PipelineState


_CONFIRMATION = "YES"

_MEMORY_META = CommandMeta(
    grammar="verb",
    group="Memory & Knowledge",
    subcommands=(
        SubCommand(
            name="stats",
            summary="Show fact counts and storage bytes",
            description=(
                "You see how many facts are committed and how much storage they "
                "occupy. Use it to gauge memory size before pruning or reindexing."
            ),
            examples=(Example(invocation="/memory stats"),),
        ),
        SubCommand(
            name="search",
            summary="Find facts by meaning",
            description=(
                "You recall committed facts by semantic similarity to your query "
                "rather than exact text match. Returns the top hits."
            ),
            args=(Arg(name="query", summary="natural-language query"),),
            examples=(Example(invocation="/memory search where do I live"),),
        ),
        SubCommand(
            name="budget",
            summary="Show storage used versus the ceiling",
            description=(
                "You compare current per-user fact storage against the configured "
                "ceiling so you know how much room is left."
            ),
            examples=(Example(invocation="/memory budget"),),
        ),
        SubCommand(
            name="reindex",
            summary="Push every committed fact to LanceDB",
            description=(
                "You rebuild the vector index from all committed facts. Use it after "
                "an embedding-model change or if semantic search looks stale."
            ),
            examples=(Example(invocation="/memory reindex"),),
        ),
        SubCommand(
            name="remember",
            summary="Stage and promote a fact",
            description=(
                "You explicitly capture a piece of text as a durable fact, bypassing "
                "the usual extraction flow."
            ),
            args=(Arg(name="text", summary="the fact text to store"),),
            examples=(Example(invocation="/memory remember I prefer terse replies"),),
        ),
        SubCommand(
            name="forget",
            summary="Delete a fact by id prefix",
            description=(
                "You remove a committed fact matched by an id prefix. Append YES to "
                "confirm — without it you only see a confirmation prompt."
            ),
            args=(
                Arg(name="fact_id_prefix", summary="leading chars of the fact id"),
                Arg(name="YES", required=False, summary="confirm the removal"),
            ),
            examples=(Example(invocation="/memory forget a1b2 YES"),),
        ),
        SubCommand(
            name="export",
            summary="Dump committed facts to a file",
            description=(
                "You write all committed facts to disk in JSON or CSV for backup or "
                "inspection outside the assistant."
            ),
            args=(
                Arg(
                    name="--format",
                    required=False,
                    summary="output format",
                    choices=("json", "csv"),
                ),
                Arg(name="--output", required=False, summary="destination path"),
            ),
            examples=(
                Example(invocation="/memory export"),
                Example(
                    invocation="/memory export --format csv --output /tmp/facts.csv",
                    note="Choose format and path",
                ),
            ),
        ),
    ),
)


class MemoryCommand(SlashCommand):
    """``/memory`` slash command — see module docstring."""

    def __init__(
        self,
        bridge: MemoryBridge | None = None,
        settings: Settings | None = None,
        db: DbPool | None = None,
        event_bus: EventBus | None = None,
        lancedb: LanceDBAdapter | None = None,
        promoter: FactPromoter | None = None,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[commands] memory.init: entry",
            extra={
                "_fields": {
                    "has_lancedb": lancedb is not None,
                    "has_promoter": promoter is not None,
                    "has_embeddings": embedding_registry is not None,
                }
            },
        )
        self._bridge: MemoryBridge = bridge  # type: ignore[assignment]  # guarded in handle()
        self._settings: Settings = settings  # type: ignore[assignment]  # guarded in handle()
        self._db: DbPool = db  # type: ignore[assignment]  # guarded in handle()
        self._bus: EventBus = event_bus  # type: ignore[assignment]  # guarded in handle()
        self._lancedb = lancedb
        self._promoter = promoter
        self._embeddings = embedding_registry
        # 4. EXIT
        log.memory.debug("[commands] memory.init: exit")

    @property
    def command(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Memory management commands (stats, search, forget, budget, reindex)."

    @property
    def meta(self) -> CommandMeta:
        return _MEMORY_META

    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        # 1. ENTRY
        log.memory.debug(
            "[commands] memory.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_id}},
        )
        if self._bridge is None or self._settings is None or self._db is None or self._bus is None:
            return "✗ /memory: not configured"
        stripped = args.strip()
        if not stripped:
            return render_usage("memory", _MEMORY_META)
        parts = stripped.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        try:
            # 2. DECISION — dispatch by subcommand
            result: str | CommandResponse
            if sub == "stats":
                result = await self._stats()
            elif sub == "search":
                result = await self._search(rest.strip())
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
            elif sub == "menu":
                result = await self._menu(rest.strip())
            else:
                log.memory.debug(
                    "[commands] memory.handle: decision — unknown subcommand",
                    extra={"_fields": {"sub": sub[:40]}},
                )
                return render_usage("memory", _MEMORY_META)
        except Exception as exc:
            # B5
            log.memory.error(
                "[commands] memory.handle: subcommand crashed",
                exc_info=exc,
                extra={"_fields": {"sub": sub}},
            )
            return f"✗ /memory {sub}: {exc}"
        # 4. EXIT
        out_text = result.text if isinstance(result, CommandResponse) else result
        log.memory.debug(
            "[commands] memory.handle: exit",
            extra={"_fields": {"sub": sub, "out_len": len(out_text)}},
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

    async def _search(self, query: str) -> str | CommandResponse:
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
        if not hits:
            return out
        actions = tuple(
            Action(
                label=h.content if len(h.content) <= 40 else h.content[:37] + "...",
                command=f"/memory menu {h.fact_id}",
                destructive=False,
            )
            for h in hits
        )
        return CommandResponse(text=out, actions=actions)

    async def _menu(self, args: str) -> str | CommandResponse:
        log.memory.debug("[commands] memory.menu: entry", extra={"_fields": {"args_len": len(args)}})
        prefix = args.split(maxsplit=1)[0] if args else ""
        if not prefix:
            return "Usage: /memory menu <fact_id_prefix>"
        fact = await find_staged_by_id(self._bridge, prefix)
        if fact is None:
            log.memory.debug("[commands] memory.menu: no match", extra={"_fields": {"prefix": prefix[:16]}})
            return f"✗ /memory menu: no fact matches prefix '{prefix}'"
        text = format_review(fact)
        actions = (
            Action(
                label=f"Forget {fact.fact_id[:8]}",
                command=f"/memory forget {fact.fact_id} {_CONFIRMATION}",
                destructive=True,
            ),
        )
        log.memory.debug("[commands] memory.menu: exit", extra={"_fields": {"fact_id": fact.fact_id}})
        return CommandResponse(text=text, actions=actions)

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
        records = await fetch_all_committed_for_reindex(self._db, self._embeddings)
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
        await forget_fact(self._bridge, fact.fact_id, actor="user:forget")
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
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> MemoryCommand:
        """Construct a :class:`MemoryCommand` and register it on the singleton."""
        cmd = cls(
            bridge=bridge,
            settings=settings,
            db=db,
            event_bus=event_bus,
            lancedb=lancedb,
            promoter=promoter,
            embedding_registry=embedding_registry,
        )
        CommandRegistry.instance().register(cmd)
        return cmd
