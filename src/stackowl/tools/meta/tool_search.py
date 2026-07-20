"""tool_search — lexical, deterministic discovery over the full tool catalog.

Provenance: see ``_bmad-output/research/tool-port-analysis.md`` (E1 tool_search).
The field-weighted lexical scorer is ported verbatim (operator vote): an exact
name match scores 20, a name substring 8, a label substring 4, a description
substring 2; ties break lexicographically by name for a total, reproducible
order. The tokenizer is **adapted to Unicode** (the prior-art's ASCII-only split
is replaced with ``\\w`` so multilingual queries tokenize intact) per the
no-hardcoded-English rule. Model-free, RNG-free, never "unavailable".

ADR-10 locks lexical/BM25 ranking (no embedding dependency at the registry layer).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from stackowl.infra import hydrated_tools
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolResult

__all__ = ["CatalogEntry", "ToolSearchTool", "rank_tools", "tokenize"]

_DEFAULT_LIMIT = 8  # operator vote
_MAX_LIMIT = 25
_SELF_NAME = "tool_search"

# Keep word chars (Unicode \w) plus the separators that appear inside tool ids
# (``_ . / : -``); split on everything else. \w is Unicode-aware in Python 3.
_TOKEN_SPLIT = re.compile(r"[^\w./:\-]+", re.UNICODE)

# Ported field weights (operator vote: verbatim).
_W_NAME_EXACT = 20
_W_NAME_INCLUDES = 8
_W_LABEL_INCLUDES = 4
_W_DESC_INCLUDES = 2


@dataclass(frozen=True)
class CatalogEntry:
    """A lightweight, scorable view of a registered tool."""

    name: str
    description: str
    category: str | None = None  # consent_category — the "label" field


def tokenize(text: str) -> list[str]:
    """Lowercase + split into Unicode-safe terms (multilingual)."""
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if t]


def _score_entry(entry: CatalogEntry, terms: list[str]) -> int:
    """Field-weighted lexical score (ported weights). 0 when no term matches."""
    if not terms:
        # Deliberate divergence from the port source (which returns 1 to list all
        # on an empty query): rank_tools early-returns [] on empty terms, so this
        # path is never hit live. Do NOT "fix" back to 1 — it would resurrect
        # match-everything-on-empty-query.
        return 0
    name = entry.name.lower()
    label = (entry.category or "").lower()
    description = entry.description.lower()
    score = 0
    for term in terms:
        if name == term:
            score += _W_NAME_EXACT
        if term in name:
            score += _W_NAME_INCLUDES
        if label and term in label:
            score += _W_LABEL_INCLUDES
        if term in description:
            score += _W_DESC_INCLUDES
    return score


def rank_tools(entries: list[CatalogEntry], query: str, limit: int = _DEFAULT_LIMIT) -> list[CatalogEntry]:
    """Return up to ``limit`` entries ranked by score desc, name asc (total order)."""
    # dedupe terms so a repeated word doesn't multiply a field's contribution
    terms = list(dict.fromkeys(tokenize(query)))
    if not terms:
        return []
    scored = ((entry, _score_entry(entry, terms)) for entry in entries)
    hits = [(entry, score) for entry, score in scored if score > 0]
    hits.sort(key=lambda h: (-h[1], h[0].name))
    return [entry for entry, _ in hits[: max(0, limit)]]


class ToolSearchTool(Tool):
    """Discover tools by keyword over the full catalog (read-only, self-healing)."""

    @property
    def name(self) -> str:
        return _SELF_NAME

    @property
    def description(self) -> str:
        return (
            "Search the full tool catalog by keyword and return the most relevant tools "
            "ranked by name and description match. Use this to discover a capability that "
            "is not already in your presented tool set."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords describing the capability you need."},
                "limit": {
                    "type": "integer",
                    "description": f"Max results (default {_DEFAULT_LIMIT}, capped at {_MAX_LIMIT}).",
                    "default": _DEFAULT_LIMIT,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        query = str(kwargs.get("query", ""))
        raw_limit = kwargs.get("limit", _DEFAULT_LIMIT)
        limit = min(int(raw_limit) if isinstance(raw_limit, int) else _DEFAULT_LIMIT, _MAX_LIMIT)
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.debug("tool_search.execute: entry", extra={"_fields": {"query_len": len(query), "limit": limit}})

        registry = get_services().tool_registry
        # 2. DECISION — self-healing: no registry → empty result, never raise
        if registry is None:
            log.tool.warning("tool_search.execute: no tool_registry in services — empty result")
            return ToolResult(success=True, output="(no tools available)", duration_ms=(time.monotonic() - t0) * 1000)

        # 3. STEP — build catalog (exclude self) and rank. Isolated per-entry: a
        # single tool whose .manifest/.description/.name raises (a bad subclass
        # override, a lazy-import failure, etc.) must never poison the WHOLE
        # catalog build — that turned every tool_search call into a hard failure
        # regardless of query, since this list comprehension used to have no
        # per-entry exception boundary (see tool_describe.py for the same fix).
        entries: list[CatalogEntry] = []
        for t in registry.all():
            if t.name == _SELF_NAME:
                continue
            try:
                entries.append(CatalogEntry(t.name, t.description, t.manifest.consent_category))
            except Exception as exc:
                log.tool.error(
                    "tool_search.execute: skipping tool with broken manifest",
                    exc_info=exc, extra={"_fields": {"tool": t.name}},
                )
        ranked = rank_tools(entries, query, limit)
        if ranked:
            lines = [f"- {e.name}: {e.description.splitlines()[0] if e.description else ''}" for e in ranked]
            output = "\n".join(lines)
            # FX-07 — promote these hits into the NEXT turn's presented schema
            # (see pipeline/steps/execute.py's build_tool_schemas) instead of
            # making the model re-discover the same tool every turn.
            hydrated_tools.record(TraceContext.get()["session_id"], [e.name for e in ranked])
        else:
            # Distinguish "catalog empty" from "catalog had tools but none matched"
            # so a debugging operator can tell a wiring gap from a genuine miss.
            log.tool.debug(
                "tool_search.execute: no matches",
                extra={"_fields": {"catalog_size": len(entries), "query_len": len(query)}},
            )
            output = "(no matching tools)"
        # 4. EXIT
        log.tool.info(
            "tool_search.execute: exit",
            extra={"_fields": {"results": len(ranked), "catalog_size": len(entries)}},
        )
        return ToolResult(success=True, output=output, duration_ms=(time.monotonic() - t0) * 1000)
