from __future__ import annotations

from stackowl.tools._infra.presentation import ToolPresentation


class _FakeManifest:
    def __init__(self, group): self.toolset_group = group


class _FakeTool:
    def __init__(self, name, group="misc", desc=""):
        self.name = name
        self._g = group
        self.description = desc
        self.manifest = _FakeManifest(group)


def _tools():
    return [
        _FakeTool("read_file", "io", "read a file"),
        _FakeTool("write_file", "io", "write a file"),
        _FakeTool("tool_search", "meta"),
        _FakeTool("send_email", "comms", "send an email message"),
        _FakeTool("web_search", "search", "search the web"),
        _FakeTool("calendar_create", "calendar", "create a calendar event"),
    ]


def test_no_profile_makes_all_non_guaranteed_discretionary():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None, request_text="hello",
    )
    gnames = {t.name for t in guaranteed}
    assert "read_file" in gnames and "tool_search" in gnames
    dnames = {t.name for t in disc}
    assert {"send_email", "web_search", "calendar_create"} <= dnames


def test_relevance_ranks_request_matched_tool_first():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        request_text="please send an email to my boss",
    )
    assert disc[0].name == "send_email"


def test_unmatched_tools_kept_in_deterministic_tail():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        request_text="xyzzy-no-match",
    )
    names = [t.name for t in disc]
    assert names == sorted(names)
    assert {"send_email", "web_search", "calendar_create"} <= set(names)


# --------------------------------------------------------------------------- #
# Task 4 — to_provider_schema opt-in token budget
# Uses the same ToolRegistry + real Tool subclass idiom as test_presentation.py
# --------------------------------------------------------------------------- #

from stackowl.tools.base import Tool, ToolManifest, ToolResult  # noqa: E402
from stackowl.tools.registry import ToolRegistry  # noqa: E402


class _RT(Tool):
    """Minimal real Tool subclass for ToolRegistry tests."""

    def __init__(self, name: str, *, group: str | None = None) -> None:
        self._name, self._group = name, group

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} does things"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=self._group,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


def _budget_registry() -> ToolRegistry:
    """Build a registry with enough tools that the full catalog > guaranteed set."""
    reg = ToolRegistry()
    # guaranteed base set members (always included)
    for n in ("read_file", "write_file", "tool_search", "tool_describe"):
        reg.register(_RT(n))
    # extra discretionary tools — enough to clearly exceed the guaranteed base
    for n in ("send_email", "web_search", "calendar_create", "note_tool",
              "task_create", "task_list", "task_update", "browser_open",
              "image_gen", "voice_record"):
        reg.register(_RT(n, group="misc"))
    return reg


def test_to_provider_schema_no_budget_returns_full_catalog() -> None:
    """Back-compat: no budget arg → full catalog returned (all registered tools)."""
    reg = _budget_registry()
    schemas = reg.to_provider_schema("openai")
    full = len(schemas)
    # should include every registered tool
    names = {s["function"]["name"] for s in schemas}
    assert "read_file" in names
    assert "tool_search" in names
    assert "send_email" in names
    assert full == len(reg.all())


def test_to_provider_schema_tight_budget_returns_fewer_than_full() -> None:
    """Tight budget → fit returns < full; guaranteed base names are always included."""
    reg = _budget_registry()
    schemas_no_budget = reg.to_provider_schema("openai")
    full = len(schemas_no_budget)

    # window=8192, fixed_cost_tokens=7000 → usable=7372, tool_budget=7372-2048-7000 < 0
    # → guaranteed only (budget exhausted by fixed cost before any candidate)
    schemas_budgeted = reg.to_provider_schema(
        "openai",
        request_text="hello",
        budget={"window": 8192, "fixed_cost_tokens": 7000},
    )
    assert len(schemas_budgeted) < full

    budgeted_names = {s["function"]["name"] for s in schemas_budgeted}
    # guaranteed base names that are registered must always be present
    assert "read_file" in budgeted_names
    assert "tool_search" in budgeted_names


def test_to_provider_schema_generous_budget_returns_all() -> None:
    """Generous budget → all tools fit → same count as no-budget call."""
    reg = _budget_registry()
    full = len(reg.to_provider_schema("openai"))
    schemas_budgeted = reg.to_provider_schema(
        "openai",
        request_text="hello",
        budget={"window": 200_000, "fixed_cost_tokens": 100},
    )
    assert len(schemas_budgeted) == full


def test_to_provider_schema_budget_anthropic_protocol() -> None:
    """Budget path works for the anthropic protocol (name at schema root)."""
    reg = _budget_registry()
    schemas_no_budget = reg.to_provider_schema("anthropic")
    full = len(schemas_no_budget)

    schemas_budgeted = reg.to_provider_schema(
        "anthropic",
        request_text="hi",
        budget={"window": 8192, "fixed_cost_tokens": 7000},
    )
    assert len(schemas_budgeted) < full
    budgeted_names = {s["name"] for s in schemas_budgeted}
    assert "read_file" in budgeted_names
    assert "tool_search" in budgeted_names
