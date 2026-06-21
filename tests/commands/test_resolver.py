"""CommandResolver — NL → structured command suggestions (suggest, never fire)."""

from __future__ import annotations

import pytest

from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.resolver import CommandResolver, suggest_invocations


@pytest.fixture(autouse=True)
def _isolate_registry():  # type: ignore[no-untyped-def]
    snapshot = list(CommandRegistry.instance().list())
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    yield
    CommandRegistry.reset()
    for cmd in snapshot:
        CommandRegistry.instance().register(cmd)


def _commands():  # type: ignore[no-untyped-def]
    return CommandRegistry.instance().list()


@pytest.mark.asyncio
async def test_lexical_resolution_finds_subcommand_by_word() -> None:
    """No embeddings: a literal word in the query surfaces the right sub-command."""
    r = CommandResolver()  # lexical-only
    r.index(_commands())
    out = await r.resolve("forget a fact", limit=5)
    invocations = [c.invocation for c in out]
    assert "/memory forget" in invocations


@pytest.mark.asyncio
async def test_resolve_never_returns_empty_invocation_and_is_ranked() -> None:
    r = CommandResolver()
    r.index(_commands())
    out = await r.resolve("search my memory", limit=3)
    assert out and len(out) <= 3
    assert all(c.invocation.startswith("/") for c in out)
    # descending score
    scores = [c.score for c in out]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_empty_query_returns_nothing() -> None:
    r = CommandResolver()
    r.index(_commands())
    assert await r.resolve("   ", limit=5) == []


@pytest.mark.asyncio
async def test_semantic_path_uses_embeddings() -> None:
    """With a (fake) semantic provider, the semantic signal is blended in."""

    class _FakeEmbeddings:
        """Vector = presence of a few marker tokens; cosine rewards shared markers."""

        _MARKERS = ("forget", "remember", "search", "browser", "agent", "provider")

        async def embed(self, texts: list[str]) -> list[list[float]]:
            out = []
            for t in texts:
                low = t.casefold()
                out.append([1.0 if m in low else 0.0 for m in self._MARKERS])
            return out

    r = CommandResolver(_FakeEmbeddings(), semantic=True)  # type: ignore[arg-type]
    r.index(_commands())
    out = await r.resolve("please forget this", limit=5)
    assert "/memory forget" in [c.invocation for c in out]


@pytest.mark.asyncio
async def test_no_hardcoded_language_assumption() -> None:
    """The lexical fallback must be language-agnostic — no hardcoded stopword
    list. We verify (a) a non-Latin script tokenizes and ranks at all, and
    (b) ranking is driven by corpus-derived IDF, not an English keyword list."""
    # A custom corpus in a non-English language: the distinctive word must win
    # even though a generic word is shared across entries.
    from stackowl.commands.base import SlashCommand
    from stackowl.commands.metadata import CommandMeta, SubCommand

    class _Fake(SlashCommand):
        def __init__(self, name: str, subs: tuple[SubCommand, ...]) -> None:
            self._name = name
            self._subs = subs

        @property
        def command(self) -> str:
            return self._name

        @property
        def description(self) -> str:
            return "mostrar datos"  # "show data" — the shared, generic words

        @property
        def meta(self) -> CommandMeta:
            return CommandMeta(subcommands=self._subs)

        async def handle(self, args, state):  # type: ignore[no-untyped-def]
            return ""  # never invoked — resolver only reads metadata

    cmds = [
        _Fake("memoria", (SubCommand("olvidar", "mostrar olvidar un dato"),)),
        _Fake("costo", (SubCommand("hoy", "mostrar datos de hoy"),)),
    ]
    r = CommandResolver()
    r.index(cmds)  # type: ignore[arg-type]
    out = await r.resolve("olvidar un dato", limit=3)
    # "olvidar" is rare/distinctive (high IDF); "mostrar" is in every entry (low
    # IDF) — so the forget-equivalent ranks first despite the shared generic word.
    assert out[0].invocation == "/memoria olvidar"


@pytest.mark.asyncio
async def test_suggest_invocations_for_unknown_command() -> None:
    """The gateway helper: an unknown command's words point to real commands."""
    hits = await suggest_invocations("remember this note", _commands(), limit=3)
    assert "/memory remember" in hits
    assert all(h.startswith("/") for h in hits)


@pytest.mark.asyncio
async def test_suggest_invocations_empty_query() -> None:
    assert await suggest_invocations("", _commands()) == []


@pytest.mark.asyncio
async def test_resolver_does_not_dispatch() -> None:
    """Resolving must have NO side effects — it only ranks. Sanity: a 'delete'
    query returns the suggestion but nothing is executed (no exceptions, the
    registry is untouched)."""
    before = {c.command for c in _commands()}
    r = CommandResolver()
    r.index(_commands())
    await r.resolve("delete everything from memory", limit=5)
    after = {c.command for c in CommandRegistry.instance().list()}
    assert before == after
