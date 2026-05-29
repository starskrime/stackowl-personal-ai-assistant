"""E1-S1 — tool_search lexical scorer + tool.

Ported scorer weights (name exact=20, name-includes=8, label=4, description=2);
Unicode-safe tokenizer (multilingual, not ASCII-only); deterministic, model-free.
"""

from __future__ import annotations

from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.meta.tool_search import (
    CatalogEntry,
    ToolSearchTool,
    rank_tools,
    tokenize,
)
from stackowl.tools.registry import ToolRegistry


# --------------------------------------------------------------------------- #
# tokenizer
# --------------------------------------------------------------------------- #
def test_tokenize_splits_on_whitespace_and_punct() -> None:
    assert tokenize("read a pdf") == ["read", "a", "pdf"]


def test_tokenize_keeps_path_and_separator_chars() -> None:
    # '_', '.', '/', ':', '-' are kept inside a token (matches the ported scorer)
    assert tokenize("read_file") == ["read_file"]
    assert tokenize("mcp.fixture.tool") == ["mcp.fixture.tool"]


def test_tokenize_is_unicode_multilingual() -> None:
    # Must NOT be ASCII-only — accented / non-Latin queries tokenize intact.
    assert tokenize("café Café") == ["café", "café"]
    assert tokenize("поиск файла") == ["поиск", "файла"]


def test_tokenize_lowercases_and_drops_empties() -> None:
    assert tokenize("  PDF   Reader  ") == ["pdf", "reader"]


# --------------------------------------------------------------------------- #
# scorer / ranking
# --------------------------------------------------------------------------- #
_CATALOG = [
    CatalogEntry("pdf", "Read and extract text from a PDF document"),
    CatalogEntry("read_file", "Read the contents of a file from disk"),
    CatalogEntry("cronjob", "Schedule a reminder or recurring task"),
    CatalogEntry("shell", "Execute a shell command"),
]


def test_rank_pdf_query_ranks_pdf_and_read_above_unrelated() -> None:
    ranked = rank_tools(_CATALOG, "read a pdf", limit=8)
    names = [e.name for e in ranked]
    assert names[0] == "pdf"  # exact name match (20) + includes (8) + desc
    assert "read_file" in names
    assert names.index("read_file") < names.index("shell") if "shell" in names else True


def test_rank_schedule_query_surfaces_cronjob() -> None:
    ranked = rank_tools(_CATALOG, "schedule a reminder", limit=8)
    assert "cronjob" in [e.name for e in ranked]


def test_rank_empty_query_returns_empty() -> None:
    assert rank_tools(_CATALOG, "", limit=8) == []
    assert rank_tools(_CATALOG, "   ", limit=8) == []


def test_rank_no_match_returns_empty() -> None:
    assert rank_tools(_CATALOG, "zzzznomatch", limit=8) == []


def test_rank_is_deterministic_and_total_order() -> None:
    # Two entries with identical score must tiebreak by name (lexicographic) —
    # same query + catalog → identical ordering every time.
    cat = [
        CatalogEntry("bravo", "duplicate scoring token xyz"),
        CatalogEntry("alpha", "duplicate scoring token xyz"),
    ]
    r1 = [e.name for e in rank_tools(cat, "xyz", limit=8)]
    r2 = [e.name for e in rank_tools(cat, "xyz", limit=8)]
    assert r1 == r2 == ["alpha", "bravo"]  # equal score → name asc


def test_rank_respects_limit() -> None:
    cat = [CatalogEntry(f"tool{i}", "match token") for i in range(20)]
    assert len(rank_tools(cat, "match", limit=5)) == 5


def test_label_field_contributes_to_score() -> None:
    # consent_category acts as the label slot (+4 on include)
    cat = [
        CatalogEntry("x", "no relevant words", category="lock"),
        CatalogEntry("y", "no relevant words"),
    ]
    ranked = rank_tools(cat, "lock", limit=8)
    assert [e.name for e in ranked] == ["x"]


# --------------------------------------------------------------------------- #
# the tool over a real ToolRegistry
# --------------------------------------------------------------------------- #
class _StubTool(Tool):
    def __init__(self, name: str, desc: str) -> None:
        self._name, self._desc = name, desc

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="x", duration_ms=1.0)


async def test_tool_searches_registry_and_excludes_itself() -> None:
    from stackowl.pipeline.services import StepServices, reset_services, set_services

    reg = ToolRegistry()
    reg.register(_StubTool("pdf", "extract text from a pdf"))
    reg.register(_StubTool("cronjob", "schedule a reminder"))
    reg.register(ToolSearchTool())
    token = set_services(StepServices(tool_registry=reg))
    try:
        result = await ToolSearchTool().execute(query="pdf")
    finally:
        reset_services(token)
    assert result.success
    assert "pdf" in result.output
    assert "tool_search" not in result.output  # excludes itself


async def test_tool_no_registry_is_self_healing() -> None:
    # No services wired → returns an empty-but-successful result, never raises.
    result = await ToolSearchTool().execute(query="pdf")
    assert result.success
    assert result.error is None


def test_tool_severity_is_read() -> None:
    assert ToolSearchTool().manifest.action_severity == "read"
