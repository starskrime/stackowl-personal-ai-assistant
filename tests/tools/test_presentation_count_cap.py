"""to_provider_schema honors the configurable tool-count cap (Phase 0).

A weak model is offered fewer tools even when more would fit in tokens, via the
budget dict's optional "max_tools" (OrchestratorSettings.tool_count_cap). When
"max_tools" is absent the cap defaults to 40 → byte-identical (FR5 preserved).
Mirrors the real Tool/ToolRegistry idiom from test_presentation_budget.py.
"""

from __future__ import annotations

from stackowl.config.settings import OrchestratorSettings
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _RT(Tool):
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


def _wide_registry() -> ToolRegistry:
    """4 guaranteed base tools + 16 discretionary → 20 total (exceeds the test caps)."""
    reg = ToolRegistry()
    for n in ("read_file", "write_file", "tool_search", "tool_describe"):
        reg.register(_RT(n))
    for i in range(16):
        reg.register(_RT(f"misc_tool_{i:02d}", group="misc"))
    return reg


def _budget(window: int, *, max_tools: int | None) -> dict[str, int]:
    # tiny fixed cost so the TOKEN budget never binds — only the COUNT cap can.
    b: dict[str, int] = {"window": window, "fixed_cost_tokens": 100}
    if max_tools is not None:
        b["max_tools"] = max_tools
    return b


def test_low_max_tools_caps_the_roster() -> None:
    reg = _wide_registry()
    schemas = reg.to_provider_schema(
        "openai", budget=_budget(16384, max_tools=12),
    )
    assert len(schemas) == 12
    names = {s["function"]["name"] for s in schemas}
    assert "read_file" in names and "tool_search" in names  # guaranteed kept


def test_absent_max_tools_is_byte_identical_full_catalog() -> None:
    reg = _wide_registry()
    full = len(reg.to_provider_schema("openai"))
    schemas = reg.to_provider_schema(
        "openai", budget=_budget(16384, max_tools=None),
    )
    assert len(schemas) == full == 20  # the default ceiling ≥ 20 → all presented


def test_the_default_lets_a_capable_model_see_the_whole_catalogue() -> None:
    """FR5 — capable model = FULL SET. That was this test's stated intent all along,
    and the number it pinned had stopped delivering it.

    It asserted `== 40`, described as "byte-identical", and that was true when the
    catalogue was ~20 tools. The catalogue reached 79 and 40 became a clip: measured
    2026-09-08, 1,491 live turns presented all 79 only because this box's yaml
    overrides the cap by hand. The default now REFERENCES the backstop the owner
    decision sized (`HARD_TOOL_COUNT_CAP`, "comfortably above any real toolset"), so
    the intent holds as the catalogue grows instead of expiring in silence.
    """
    from stackowl.pipeline.context_budget import HARD_TOOL_COUNT_CAP

    assert OrchestratorSettings().tool_count_cap == HARD_TOOL_COUNT_CAP
