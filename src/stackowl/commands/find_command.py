"""FindCommand — ``/find <natural language>`` → ranked structured commands.

The honest surface for the semantic :class:`CommandResolver`: a user describes
what they want ("forget what I said about my sister") and gets the exact
slash-commands that do it, ready to type or edit.  It SUGGESTS — it never runs
anything.  Works on every channel (TUI and Telegram alike), degrading to lexical
matching when no semantic embedding model is available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, Example, render_usage
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.resolver import CommandResolver
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.pipeline.state import PipelineState

_FIND_META = CommandMeta(
    grammar="flag",
    group="Help & Discovery",
    args=(Arg(name="query", repeat=True, summary="describe what you want to do"),),
    examples=(
        Example(invocation="/find forget what I told you about my sister"),
        Example(invocation="/find show how much I've spent today"),
    ),
)


class FindCommand(SlashCommand):
    """``/find <query>`` — natural-language command discovery (suggest, never run)."""

    def __init__(self, embedding_registry: EmbeddingRegistry | None = None) -> None:
        log.gateway.debug("[commands] find.init: entry")
        self._embedding_registry = embedding_registry
        self._resolver: CommandResolver | None = None
        self._indexed_count = -1

    @property
    def command(self) -> str:
        return "find"

    @property
    def description(self) -> str:
        return "Find a command by describing what you want in plain language."

    @property
    def meta(self) -> CommandMeta:
        return _FIND_META

    def _ensure_resolver(self, commands: list[SlashCommand]) -> CommandResolver:
        """Build/refresh the resolver, reusing cached embeddings across calls."""
        if self._resolver is not None and self._indexed_count == len(commands):
            return self._resolver
        provider = None
        semantic = False
        if self._embedding_registry is not None:
            try:
                provider = self._embedding_registry.get()
                semantic = self._embedding_registry.is_semantic
            except Exception as exc:  # degrade to lexical, never crash
                log.gateway.warning("[commands] find: embedding registry unavailable", exc_info=exc)
        resolver = CommandResolver(provider, semantic=semantic)
        resolver.index(commands)
        self._resolver = resolver
        self._indexed_count = len(commands)
        log.gateway.debug(
            "[commands] find: resolver ready",
            extra={"_fields": {"commands": len(commands), "semantic": semantic}},
        )
        return resolver

    async def handle(self, args: str, state: PipelineState) -> str:
        query = args.strip()
        log.gateway.debug(
            "[commands] find.handle: entry",
            extra={"_fields": {"query_len": len(query), "session": state.session_id}},
        )
        if not query:
            return render_usage("find", _FIND_META)

        commands = CommandRegistry.instance().list()
        resolver = self._ensure_resolver(commands)
        candidates = await resolver.resolve(query, limit=5)

        if not candidates:
            return f"No command matched '{query}'. Try /help to browse what's available."

        width = max(len(c.invocation) for c in candidates)
        lines = [f"For '{query}', did you mean:", ""]
        for c in candidates:
            lines.append(f"  {c.invocation:<{width}}  {c.summary}")
        lines.append("")
        lines.append("Type or edit one of these and press enter to run it.")
        log.gateway.debug(
            "[commands] find.handle: exit", extra={"_fields": {"returned": len(candidates)}}
        )
        return "\n".join(lines)
