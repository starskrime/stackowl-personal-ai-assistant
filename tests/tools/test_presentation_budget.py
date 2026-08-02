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
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
    )
    gnames = {t.name for t in guaranteed}
    assert "read_file" in gnames and "tool_search" in gnames
    dnames = {t.name for t in disc}
    assert {"send_email", "web_search", "calendar_create"} <= dnames


# --------------------------------------------------------------------------- #
# D05.2 — the ordering signal changed, and this block is where that is pinned.
#
# REPLACED: test_relevance_ranks_request_matched_tool_first, which asserted that
# request_text="please send an email to my boss" put send_email first. That
# behaviour is GONE ON PURPOSE, not broken: making the presented array a function
# of the question is what made it change every turn (D01.3 measured 15 change
# events across 5 lanes) and what defeats D01.2's position-0 cache marker. The
# replacement below asserts the opposite property — the question must NOT be able
# to reorder the array — plus the new signal that does.
# --------------------------------------------------------------------------- #


def test_usage_ranks_the_owls_most_used_tool_first():
    """The learned per-owl signal orders the discretionary set."""
    _, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        usage_scores={"web_search": 9.0, "send_email": 3.0},
    )
    names = [t.name for t in disc]
    assert names[0] == "web_search"
    assert names[1] == "send_email"
    # Unscored tools keep the deterministic by-name tail — none are dropped.
    assert "calendar_create" in names


def test_ordering_is_independent_of_the_request_text():
    """THE D05.2 ACCEPTANCE PROPERTY.

    rank_candidates no longer takes the request at all, so two different
    questions in one session cannot produce two different arrays. Asserted on
    the call signature by construction AND on the output, because a future
    change that reintroduced a text-derived tiebreak would still type-check.
    """
    scores = {"web_search": 2.0}
    first = [t.name for t in ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        usage_scores=scores,
    )[1]]
    second = [t.name for t in ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        usage_scores=scores,
    )[1]]
    assert first == second
    import inspect
    assert "request_text" not in inspect.signature(
        ToolPresentation.rank_candidates
    ).parameters


def test_equal_scores_break_by_name_not_by_registry_order():
    """Two tools on an equal score must not order by list position.

    Registry iteration order is not a contract, so a sort keyed on score alone
    would make the array depend on registration sequence — stable only by luck,
    and silently unstable the day a tool is registered somewhere else.
    """
    tools = _tools()
    scores = {"web_search": 5.0, "send_email": 5.0, "calendar_create": 5.0}
    forward = [t.name for t in ToolPresentation().rank_candidates(
        all_tools=tools, profile=None, pins=None, hydrated=None, usage_scores=scores,
    )[1]]
    reverse = [t.name for t in ToolPresentation().rank_candidates(
        all_tools=list(reversed(tools)), profile=None, pins=None, hydrated=None,
        usage_scores=scores,
    )[1]]
    assert forward == reverse
    assert forward[:3] == ["calendar_create", "send_email", "web_search"]


def test_unscored_tools_kept_in_deterministic_tail():
    """No usage history at all → pure by-name order. The cold-start path, and a
    real fallback rather than a degraded one: it is still stable across turns."""
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        usage_scores=None,
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


# --------------------------------------------------------------------------- #
# PRE-EXISTING FAILURE, fixed here (found while running D05.2's suite; confirmed
# red at HEAD 2db29222 on an untouched worktree, so NOT caused by D05.2).
#
# Both tests below carried arithmetic from a budget model that no longer exists:
# "usable=7372, tool_budget=7372-2048-7000 < 0 → guaranteed only". The 0.9 safety
# fraction and the 2048-token response reserve were BOTH removed on 2026-07-22 by
# owner decision (see context_budget.tool_budget_tokens: "No artificial
# safety-fraction/response-reserve shrinkage"). The tests were never updated, so:
#
#     measured: tool_budget_tokens(window=8192, fixed_cost_tokens=7000) = 1192
#               14 test schemas cost ~518 tokens total
#
# — everything fit, and "tight budget returns fewer than full" asserted 14 < 14.
# The tests were not detecting a regression; they were asserting deleted
# behaviour. Fixed by making the budget genuinely bind under the CURRENT model
# rather than by relaxing the assertion, which would have deleted the coverage.
# --------------------------------------------------------------------------- #


def test_to_provider_schema_tight_budget_returns_fewer_than_full() -> None:
    """Tight budget → fit returns < full; guaranteed base names are always included."""
    reg = _budget_registry()
    schemas_no_budget = reg.to_provider_schema("openai")
    full = len(schemas_no_budget)

    # window=8192, fixed_cost_tokens=8000 → tool budget = 192 tokens. The four
    # guaranteed schemas (~37 tokens each) consume it, so few or no discretionary
    # candidates fit. Guaranteed is never dropped even when the budget goes
    # negative — that is the property under test.
    schemas_budgeted = reg.to_provider_schema(
        "openai",
        budget={"window": 8192, "fixed_cost_tokens": 8000},
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
        budget={"window": 8192, "fixed_cost_tokens": 8000},
    )
    assert len(schemas_budgeted) < full
    budgeted_names = {s["name"] for s in schemas_budgeted}
    assert "read_file" in budgeted_names
    assert "tool_search" in budgeted_names
