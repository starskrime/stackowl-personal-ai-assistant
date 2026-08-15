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
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import CommandResponse
from stackowl.infra.observability import log
from stackowl.memory.curated import USER_TARGET, CuratedMemory

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.pipeline.state import PipelineState


_CONFIRMATION = "YES"

_MEMORY_META = CommandMeta(
    grammar="verb",
    group="Memory & Knowledge",
    subcommands=(
        SubCommand(
            name="stats",
            summary="Show what is in curated memory, per file",
            description=(
                "You see how many entries each curated file holds and how full it "
                "is — your profile and each owl's own working notes."
            ),
            examples=(Example(invocation="/memory stats"),),
        ),
        SubCommand(
            name="search",
            summary="Find curated entries containing text",
            description=(
                "You search your profile and the owls' notes for entries containing "
                "the text. Substring, not semantic: a few dozen short lines do not "
                "need a vector index."
            ),
            args=(Arg(name="query", summary="text to look for"),),
            examples=(Example(invocation="/memory search jetson"),),
        ),
        SubCommand(
            name="budget",
            summary="Show how full each curated file is",
            description=(
                "You see each file's fill against its hard character budget. This is "
                "the limit that actually binds — when a file is full the agent must "
                "consolidate before it can add anything."
            ),
            examples=(Example(invocation="/memory budget"),),
        ),
        SubCommand(
            name="remember",
            summary="Write an entry into your profile",
            description=(
                "You add a durable line to USER.md, under the same character budget "
                "as the agent — so it can refuse you too. Prefix the text with "
                "--until-changed for something you expect to change."
            ),
            args=(Arg(name="text", summary="what to remember"),),
            examples=(
                Example(invocation="/memory remember I prefer terse replies"),
                Example(invocation="/memory remember --until-changed I use uv, not npm"),
            ),
        ),
        SubCommand(
            name="forget",
            summary="Remove an entry from your profile",
            description=(
                "You drop the entry containing the given text. The file is plain "
                "text, so editing it directly does the same job."
            ),
            args=(Arg(name="text", summary="text from the entry to remove"),),
            examples=(Example(invocation="/memory forget terse replies"),),
        ),
        SubCommand(
            name="export",
            summary="Print the curated files verbatim",
            description=(
                "You see exactly what the model is told about you, byte for byte. "
                "Verbatim on purpose — reformatting would break the contract that "
                "what you read is what it reads."
            ),
            examples=(
                Example(invocation="/memory export"),
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
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[commands] memory.init: entry",
            extra={
                "_fields": {
                    "has_embeddings": embedding_registry is not None,
                }
            },
        )
        self._bridge: MemoryBridge = bridge  # type: ignore[assignment]  # guarded in handle()
        self._settings: Settings = settings  # type: ignore[assignment]  # guarded in handle()
        self._db: DbPool = db  # type: ignore[assignment]  # guarded in handle()
        self._bus: EventBus = event_bus  # type: ignore[assignment]  # guarded in handle()
        # `self._lancedb = lancedb` stood here — ASSIGNED AND NEVER READ, wired all
        # the way from the orchestrator through CommandDeps for nothing. The third
        # instance of that exact shape in this programme, after MemoryCommand's
        # `promoter` and MemoryComponents' `promoter`.
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
            extra={"_fields": {"args_len": len(args), "session": state.session_key}},
        )
        # NO DEPENDENCY GUARD ANY MORE, and that is the point (D08.1). Every
        # subcommand now reads or writes two text files; none of them touches
        # SQLite, LanceDB, Kuzu or the event bus. Refusing to show you your own
        # profile because a database is unavailable would be exactly the
        # coupling this item removed.
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

    def _curated(self) -> CuratedMemory:
        return CuratedMemory()

    def _targets(self) -> list[str]:
        """`user` plus every owl that has notes. Never raises."""
        out = [USER_TARGET]
        try:
            base = self._curated().path_for(USER_TARGET).parent
            out += sorted(p.stem for p in base.glob("*.md") if p.name != "USER.md")
        except Exception as exc:  # B5 — a listing failure costs the owl half only
            log.memory.warning("[commands] memory: could not list owl notes", exc_info=exc)
        return out

    async def _stats(self) -> str:
        """What is actually in curated memory, per file.

        Retargeted in D08.1: this used to count rows in a fact store that no
        longer has any. Curated memory is what the prompt carries, so it is what
        a `stats` command should describe.
        """
        log.memory.debug("[commands] memory.stats: entry")
        mem = self._curated()
        lines = ["Curated memory:"]
        total = 0
        for target in self._targets():
            entries = mem.entries(target)
            total += len(entries)
            used, budget = mem.used_chars(target), mem.budget_for(target)
            pct = int(used / budget * 100) if budget else 0
            label = "USER.md" if target == USER_TARGET else f"{target}.md"
            lines.append(f"  {label:<22} {len(entries):>3} entries   {pct:>3}% ({used:,}/{budget:,} chars)")
        if total == 0:
            lines.append("  (empty — nothing has been remembered yet)")
        log.memory.debug("[commands] memory.stats: exit", extra={"_fields": {"entries": total}})
        return "\n".join(lines)

    async def _search(self, query: str) -> str:
        """Search curated memory. Substring, not semantic.

        A few dozen short lines do not need BM25 and cosine; scoring them would
        be precision theatre. The fact store this used to search is empty.
        """
        log.memory.debug("[commands] memory.search: entry",
                         extra={"_fields": {"query_len": len(query)}})
        if not query:
            return "Usage: /memory search <query>"
        needle = query.casefold()
        mem = self._curated()
        hits = [
            (target, e.text)
            for target in self._targets()
            for e in mem.entries(target)
            if needle in e.text.casefold()
        ]
        log.memory.debug("[commands] memory.search: exit",
                         extra={"_fields": {"hits": len(hits)}})
        if not hits:
            return f"No curated entries matching {query!r}."
        return "\n".join(
            [f"{len(hits)} match(es):"]
            + [f"  [{t}] {text}" for t, text in hits]
        )
    async def _budget(self) -> str:
        """How full the two curated files are.

        Retargeted in D08.1, and useful for the first time: it used to report
        bytes against per_user_ceiling_bytes for a store nothing read. These are
        the numbers that actually bind — when a file is full the agent must
        consolidate before it can add anything.
        """
        log.memory.debug("[commands] memory.budget: entry")
        mem = self._curated()
        lines = []
        for target in self._targets():
            used, budget = mem.used_chars(target), mem.budget_for(target)
            pct = int(used / budget * 100) if budget else 0
            bar = "#" * (pct // 10) + "." * (10 - pct // 10)
            label = "USER.md" if target == USER_TARGET else f"{target}.md"
            lines.append(f"  {label:<22} [{bar}] {pct:>3}%  {used:,}/{budget:,} chars")
        log.memory.debug("[commands] memory.budget: exit")
        return "Curated memory budget:\n" + "\n".join(lines)
    async def _remember(self, text: str) -> str:
        """Write an entry to YOUR profile, under the same budget as the agent.

        D08.1 R8Q29: one curated surface, one budget, one thing to read — so this
        can refuse you exactly as it refuses the agent. Durability defaults to
        `permanent`: a person writing about themselves is stating something they
        expect to stay true, and the entry is a line of text they can edit.
        """
        log.memory.debug("[commands] memory.remember: entry",
                         extra={"_fields": {"text_len": len(text)}})
        stripped = text.strip()
        if not stripped:
            return "Usage: /memory remember <text>"
        durability = "permanent"
        if stripped.startswith("--until-changed "):
            durability, stripped = "until_changed", stripped[len("--until-changed "):].strip()
        result = self._curated().add(USER_TARGET, stripped, durability)
        log.memory.info("[commands] memory.remember: exit",
                        extra={"_fields": {"ok": result.ok, "usage": result.usage}})
        mark = "✓" if result.ok else "✗"
        return f"{mark} {result.message} ({result.usage})"
    async def _forget(self, args: str) -> str:
        """Remove a curated entry by substring."""
        log.memory.debug("[commands] memory.forget: entry",
                         extra={"_fields": {"args_len": len(args)}})
        text = args.strip()
        if not text:
            return "Usage: /memory forget <text from the entry>"
        result = self._curated().remove(USER_TARGET, text)
        log.memory.info("[commands] memory.forget: exit",
                        extra={"_fields": {"ok": result.ok}})
        mark = "✓" if result.ok else "✗"
        return f"{mark} {result.message}"
    async def _export(self, args: str) -> str:
        """Print the curated files verbatim.

        Verbatim on purpose: the whole point of a file is that what you read is
        what the model is told. Reformatting would quietly break that.
        """
        log.memory.debug("[commands] memory.export: entry",
                         extra={"_fields": {"args": args[:40]}})
        mem = self._curated()
        out = []
        for target in self._targets():
            path = mem.path_for(target)
            if not path.exists():
                continue
            out.append(f"--- {path} ---")
            out.append(path.read_text(encoding="utf-8").rstrip())
        log.memory.debug("[commands] memory.export: exit",
                         extra={"_fields": {"files": len(out) // 2}})
        return "\n".join(out) if out else "Curated memory is empty."
    # ----- factory -------------------------------------------------------------

    @classmethod
    def create_and_register(
        cls,
        bridge: MemoryBridge,
        settings: Settings,
        db: DbPool,
        event_bus: EventBus,
        embedding_registry: EmbeddingRegistry | None = None,
    ) -> MemoryCommand:
        """Construct a :class:`MemoryCommand` and register it on the singleton."""
        cmd = cls(
            bridge=bridge,
            settings=settings,
            db=db,
            event_bus=event_bus,
            embedding_registry=embedding_registry,
        )
        CommandRegistry.instance().register(cmd)
        return cmd
