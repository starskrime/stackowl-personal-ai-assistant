"""E1-S2 — tool_describe: return a named tool's full schema (JSON, per vote)."""

from __future__ import annotations

import json

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.meta.tool_describe import ToolDescribeTool
from stackowl.tools.registry import ToolRegistry


class _StubTool(Tool):
    def __init__(self, name: str, severity: str = "read", category: str | None = None) -> None:
        self._name, self._severity, self._category = name, severity, category

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Does {self._name}."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description, parameters=self.parameters,
            action_severity=self._severity, consent_category=self._category,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


async def _describe(name: str, *tools: Tool) -> ToolResult:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    token = set_services(StepServices(tool_registry=reg))
    try:
        return await ToolDescribeTool().execute(name=name)
    finally:
        reset_services(token)


async def test_describe_returns_schema_fields() -> None:
    result = await _describe("web_search", _StubTool("web_search", category="net"))
    assert result.success
    payload = json.loads(result.output)
    assert payload["name"] == "web_search"
    assert payload["description"]
    assert payload["parameters"]["properties"]["x"]["type"] == "string"
    assert payload["action_severity"] == "read"
    assert payload["consent_category"] == "net"


async def test_describe_consequential_surfaces_severity() -> None:
    result = await _describe("danger", _StubTool("danger", severity="consequential"))
    payload = json.loads(result.output)
    assert payload["action_severity"] == "consequential"


async def test_describe_unknown_tool_is_structured_not_raise() -> None:
    result = await _describe("nonexistent", _StubTool("real"))
    assert result.success is False
    assert "nonexistent" in (result.error or "")


async def test_describe_no_registry_is_self_healing() -> None:
    result = await ToolDescribeTool().execute(name="x")
    assert result.success is False
    assert result.error is not None


class _BrokenManifestTool(_StubTool):
    """A tool whose .manifest raises AFTER registration — register() itself
    reads .manifest once at registration time, so a permanently-broken
    manifest could never even get registered; this models the more realistic
    intermittent case. Regression for the shared root cause with tool_search
    (see test_tool_search.py's matching test)."""

    def __init__(self, *a: object, **kw: object) -> None:
        super().__init__(*a, **kw)  # type: ignore[arg-type]
        self.broken = False

    @property
    def manifest(self) -> ToolManifest:  # type: ignore[override]
        if self.broken:
            raise RuntimeError("simulated broken manifest")
        return super().manifest


async def test_describe_broken_manifest_is_structured_not_raise() -> None:
    reg = ToolRegistry()
    broken_tool = _BrokenManifestTool("broken")
    reg.register(broken_tool)
    broken_tool.broken = True  # degrade AFTER registration succeeded
    token = set_services(StepServices(tool_registry=reg))
    try:
        result = await ToolDescribeTool().execute(name="broken")
    finally:
        reset_services(token)
    assert result.success is False
    assert "broken" in (result.error or "")


async def test_describe_tool_with_no_params() -> None:
    class _NoParams(_StubTool):
        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

    result = await _describe("bare", _NoParams("bare"))
    payload = json.loads(result.output)
    assert payload["parameters"]["properties"] == {}


def test_describe_severity_is_read() -> None:
    assert ToolDescribeTool().manifest.action_severity == "read"
