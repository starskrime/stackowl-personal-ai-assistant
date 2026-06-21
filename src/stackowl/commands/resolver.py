"""CommandResolver — natural-language → structured slash-command suggestions.

Victor's "resolver" core: a user types a phrase ("forget what I said about my
sister") and gets ranked *structured* commands (`/memory forget`) with the exact
invocation, so they learn the grammar by being handed it.  The resolver NEVER
executes anything — it returns candidates; committing is always the user's act.

It reuses the existing local engine (no new infra):

* the command tree (``CommandMeta``/``SubCommand``) is the corpus — every command
  and sub-command node contributes its ``summary``/``description`` text,
* semantic ranking via the local ``EmbeddingProvider`` + in-process
  ``cosine_similarity`` (the corpus is tiny — a few hundred short strings — so no
  LanceDB round-trip is needed),
* a lexical ``FuzzyMatcher``/token-overlap signal as a fallback and a boost.

When no semantic embedding model is available the resolver degrades to the
lexical signal alone — still useful, never silently broken.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import SubCommand
from stackowl.embeddings.base import EmbeddingProvider
from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import cosine_similarity

# Unicode-aware word split: \w covers letters/digits in every script, so the
# tokenizer is language-agnostic (no Latin-only assumption, no keyword list).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Relevance floor for ranked results — drops the weak tail so a query whose
# meaningful words match nothing returns few/no suggestions rather than
# alphabetically-first noise. Score-based ⇒ language-agnostic.
_ABS_FLOOR = 0.18
_REL_RATIO = 0.5


@dataclass(frozen=True)
class CommandCandidate:
    """One ranked suggestion: the exact invocation plus why it matched."""

    invocation: str  # e.g. "/memory forget"
    summary: str
    score: float
    grammar: str  # "verb" | "flag" | "leaf" — the leaf node's command grammar


# A token that IS (part of) the command's name/path is a far stronger signal
# than one that only appears in prose — the identifier is canonical. Boosting it
# is language-agnostic (it's about WHERE the word sits, not WHICH word it is), and
# it fixes the small-corpus IDF artefact where a rare function word out-weights a
# real command name.
_NAME_BOOST = 3.0


@dataclass
class _Entry:
    invocation: str
    summary: str
    grammar: str
    text: str  # the searchable blob (command + path + summary + description)
    tokens: frozenset[str]  # all tokens (name + prose)
    name_tokens: frozenset[str]  # just the command/path/sub-command-name tokens
    vector: list[float] | None = field(default=None)


def _tokens(s: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(s.casefold()))


class CommandResolver:
    """Indexes the command tree and ranks it against a natural-language query.

    Suggest-only: ``resolve`` returns candidates and never dispatches. Construct
    with an ``EmbeddingProvider`` + ``semantic=True`` for meaning-based ranking;
    omit it (or pass ``semantic=False``) for the lexical-only fallback.
    """

    def __init__(
        self, embeddings: EmbeddingProvider | None = None, *, semantic: bool = False
    ) -> None:
        self._embeddings = embeddings if semantic else None
        self._entries: list[_Entry] = []
        self._idf: dict[str, float] = {}
        self._idf_default = 1.0
        self._vectors_ready = False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(self, commands: list[SlashCommand]) -> None:
        """Flatten every command + sub-command node into the search corpus."""
        entries: list[_Entry] = []
        for cmd in commands:
            grammar = cmd.meta.grammar
            # The command itself is a candidate (its bare invocation).
            entries.append(
                self._make_entry(f"/{cmd.command}", cmd.description, grammar, cmd.command, cmd.description)
            )
            for node, path in _walk(cmd.meta.subcommands, []):
                invocation = f"/{cmd.command} {' '.join(path)}"
                name_text = " ".join([cmd.command, *path])
                blob = " ".join([cmd.command, *path, node.summary, node.description])
                entries.append(self._make_entry(invocation, node.summary, grammar, name_text, blob))
        self._entries = entries
        self._idf = _compute_idf(entries)
        # An out-of-corpus query word is maximally distinctive — treat it as if it
        # appeared in a single entry.
        n = len(entries)
        self._idf_default = math.log((n + 1) / 1) + 1 if n else 1.0
        self._vectors_ready = False
        log.gateway.debug(
            "[commands] resolver.index: corpus built",
            extra={"_fields": {"entries": len(entries), "semantic": self._embeddings is not None}},
        )

    def _make_entry(
        self, invocation: str, summary: str, grammar: str, name_text: str, blob: str
    ) -> _Entry:
        return _Entry(
            invocation=invocation,
            summary=summary or "",
            grammar=grammar,
            text=blob,
            tokens=_tokens(blob),
            name_tokens=_tokens(name_text),
        )

    # ------------------------------------------------------------------
    # Resolving
    # ------------------------------------------------------------------

    async def resolve(self, query: str, *, limit: int = 5) -> list[CommandCandidate]:
        """Return up to ``limit`` ranked candidates for a natural-language query.

        Never executes anything. Combines a semantic score (if a model is
        available) with a lexical token-overlap score so a strong literal match
        still surfaces when embeddings are weak.
        """
        q = query.strip()
        if not q or not self._entries:
            return []

        sem: dict[int, float] = {}
        if self._embeddings is not None:
            sem = await self._semantic_scores(q)

        q_tokens = _tokens(q)
        ranked: list[tuple[float, _Entry]] = []
        for i, entry in enumerate(self._entries):
            lexical = self._lexical_score(q_tokens, entry)
            semantic = sem.get(i, 0.0)
            # Blend: semantic leads when present, lexical guarantees literal hits
            # never vanish. Both already in [0, 1].
            score = (0.7 * semantic + 0.5 * lexical) if self._embeddings is not None else lexical
            if score > 0:
                ranked.append((score, entry))

        ranked.sort(key=lambda t: (-t[0], t[1].invocation))
        # Relevance floor: drop the long tail of weak matches (e.g. an entry that
        # only shares one low-information word). Keep candidates that are both
        # above an absolute floor and reasonably close to the best match — score-
        # based, so it's language-agnostic.
        if ranked:
            top = ranked[0][0]
            cutoff = max(_ABS_FLOOR, _REL_RATIO * top)
            ranked = [pair for pair in ranked if pair[0] >= cutoff]
        out = [
            CommandCandidate(
                invocation=e.invocation, summary=e.summary, score=round(s, 4), grammar=e.grammar
            )
            for s, e in ranked[:limit]
        ]
        log.gateway.debug(
            "[commands] resolver.resolve: ranked",
            extra={"_fields": {"query_len": len(q), "returned": len(out)}},
        )
        return out

    async def _semantic_scores(self, query: str) -> dict[int, float]:
        await self._ensure_vectors()
        assert self._embeddings is not None
        try:
            q_vec = (await self._embeddings.embed([query]))[0]
        except Exception as exc:  # never let ranking crash the turn
            log.gateway.warning("[commands] resolver: query embed failed", exc_info=exc)
            return {}
        scores: dict[int, float] = {}
        for i, entry in enumerate(self._entries):
            if entry.vector is None:
                continue
            sim = cosine_similarity(q_vec, entry.vector)
            if sim is not None and sim > 0:
                scores[i] = sim
        return scores

    async def _ensure_vectors(self) -> None:
        if self._vectors_ready or self._embeddings is None:
            return
        try:
            vectors = await self._embeddings.embed([e.text for e in self._entries])
        except Exception as exc:
            log.gateway.warning("[commands] resolver: corpus embed failed", exc_info=exc)
            self._vectors_ready = True  # don't retry every keystroke
            return
        for entry, vec in zip(self._entries, vectors, strict=False):
            entry.vector = vec
        self._vectors_ready = True

    def _lexical_score(self, q_tokens: frozenset[str], entry: _Entry) -> float:
        """IDF-weighted overlap — corpus-derived, so uninformative words (in ANY
        language) self-downweight without a hardcoded stopword list."""
        if not q_tokens:
            return 0.0
        overlap = q_tokens & entry.tokens
        if not overlap:
            return 0.0
        num = 0.0
        for t in overlap:
            weight = self._idf.get(t, self._idf_default)
            if t in entry.name_tokens:
                weight *= _NAME_BOOST  # the word IS this command's name/path
            num += weight
        # Normalise by the best achievable (every query token matched as a name)
        # so the score stays in [0, 1] and a name hit dominates a prose hit.
        denom = _NAME_BOOST * sum(self._idf.get(t, self._idf_default) for t in q_tokens)
        return num / denom if denom else 0.0


async def suggest_invocations(
    query: str, commands: list[SlashCommand], *, limit: int = 3
) -> list[str]:
    """Lexical command suggestions for an unknown / near-miss command.

    Sync-safe (no embedding model loaded) helper for the gateway: when a typed
    ``/command`` doesn't exist, feed the whole typed text here to surface the
    real commands the user likely meant.  Returns invocation strings.
    """
    if not query.strip() or not commands:
        return []
    resolver = CommandResolver()  # lexical-only — cheap, no model load
    resolver.index(commands)
    candidates = await resolver.resolve(query, limit=limit)
    return [c.invocation for c in candidates]


def _compute_idf(entries: list[_Entry]) -> dict[str, float]:
    """Inverse document frequency over the command corpus.

    A token in many command entries (e.g. "show", "list", or their equivalents
    in any language the commands are authored in) gets a low weight; a rare,
    distinctive token gets a high one.  This replaces a hardcoded stopword list
    with a self-calibrating, language-agnostic signal.
    """
    n = len(entries)
    if n == 0:
        return {}
    df: dict[str, int] = {}
    for entry in entries:
        for tok in entry.tokens:
            df[tok] = df.get(tok, 0) + 1
    return {tok: math.log((n + 1) / (count + 1)) + 1 for tok, count in df.items()}


def _walk(
    subs: tuple[SubCommand, ...], prefix: list[str]
) -> list[tuple[SubCommand, list[str]]]:
    """Depth-first flatten of the sub-command tree into (node, path) pairs."""
    out: list[tuple[SubCommand, list[str]]] = []
    for sub in subs:
        path = [*prefix, sub.name]
        out.append((sub, path))
        out.extend(_walk(sub.children, path))
    return out
