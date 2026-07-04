"""Test that `owl_build` is presented under a realistic budget constraint.

Covers the "Eval 6 — Wrong-tool" acceptance test scenario: a user asks
"create an agent named Brain", and the Secretary (empty profile + real pins)
under realistic budget constraints should see owl_build in the schema.
"""

from __future__ import annotations

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


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


def test_owl_build_in_provider_schema_under_realistic_budget() -> None:
    """owl_build is in the provider schema for Secretary with realistic budget.

    Secretary has empty profile + real pins (web_fetch, browser_extract,
    browser_recall_url), request_text "create an agent named Brain", and a
    realistic budget (window=200_000, fixed_cost_tokens=2000). The owl_build
    tool should be in the resulting schema.
    """
    reg = ToolRegistry.with_defaults()

    # Secretary's real pins: essential tools for general capability
    pins = ["web_fetch", "browser_extract", "browser_recall_url"]

    # Realistic budget
    budget = {"window": 200_000, "fixed_cost_tokens": 2000}

    schemas = reg.to_provider_schema(
        "anthropic",
        profile=[],  # empty profile (Secretary)
        pins=pins,
        request_text="create an agent named Brain",
        budget=budget,
    )

    schema_names = {s["name"] for s in schemas}
    assert "owl_build" in schema_names, (
        f"owl_build not in schema under realistic budget. "
        f"Got: {sorted(schema_names)}"
    )
