"""FX-08 — ToolRegistry.register() warns (never rejects) on a thin or
duplicate tool description. tool_search's scorer weighs description-match
well below name-match by explicit, voted design (tool_search.py's field
weights are NOT touched by this lint) — a weak description is a real
discoverability gap worth surfacing at registration time.
"""

from __future__ import annotations

import pytest

from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


class _StubTool(Tool):
    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


def test_register_still_succeeds_with_a_thin_description() -> None:
    """The lint is advisory only — a thin description never blocks registration."""
    reg = ToolRegistry()
    reg.register(_StubTool("thin", "short"))
    assert reg.get("thin") is not None


def test_thin_description_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="stackowl.tool")
    reg = ToolRegistry()
    reg.register(_StubTool("thin", "short desc"))
    assert any("thin tool description" in rec.message for rec in caplog.records)


def test_long_enough_description_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="stackowl.tool")
    reg = ToolRegistry()
    long_desc = " ".join(["word"] * 20)
    reg.register(_StubTool("verbose", long_desc))
    assert not any("thin tool description" in rec.message for rec in caplog.records)


def test_duplicate_description_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="stackowl.tool")
    reg = ToolRegistry()
    desc = " ".join(["shared", "description", "text"] * 6)  # long enough to skip the thin warning
    reg.register(_StubTool("first", desc))
    caplog.clear()
    reg.register(_StubTool("second", desc))
    assert any("duplicate tool description" in rec.message for rec in caplog.records)


def test_distinct_long_descriptions_do_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="stackowl.tool")
    reg = ToolRegistry()
    reg.register(_StubTool("a", " ".join(["alpha"] * 20)))
    caplog.clear()
    reg.register(_StubTool("b", " ".join(["bravo"] * 20)))
    assert not any("duplicate tool description" in rec.message for rec in caplog.records)
    assert not any("thin tool description" in rec.message for rec in caplog.records)
