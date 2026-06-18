"""E2-S3 — restrict_to narrows the presented set to planned ∪ discovery."""

from __future__ import annotations

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _T(Tool):
    def __init__(self, name: str, *, group: str | None = None, severity: str = "read") -> None:
        self._name, self._group, self._severity = name, group, severity

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self._name, parameters=self.parameters,
            action_severity=self._severity, toolset_group=self._group,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


def _registry_with(names: list[str]) -> ToolRegistry:
    r = ToolRegistry()
    for n in names:
        r.register(_T(n))
    return r


def _present_names(schemas: list[dict]) -> set[str]:  # type: ignore[type-arg]
    out = set()
    for s in schemas:
        n = s.get("name") or (s.get("function") or {}).get("name")
        if n:
            out.add(n)
    return out


# _DEFAULT_ALWAYS is {"tool_search", "tool_describe"} — the non-evictable discovery pair.
_DISCOVERY = {"tool_search", "tool_describe"}


def test_restrict_to_none_is_unchanged() -> None:
    r = _registry_with(["tool_search", "tool_describe", "read_file", "shell", "alpha"])
    base = _present_names(r.to_provider_schema("anthropic"))
    same = _present_names(r.to_provider_schema("anthropic", restrict_to=None))
    assert base == same


def test_restrict_to_empty_yields_discovery_only() -> None:
    """An empty plan (frozenset()) MUST yield only discovery — never fall back to base+groups."""
    r = _registry_with(["tool_search", "tool_describe", "read_file", "shell", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset()))
    assert out == _DISCOVERY


def test_restrict_to_set_is_planned_plus_discovery() -> None:
    r = _registry_with(["tool_search", "tool_describe", "read_file", "shell", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset({"alpha"})))
    assert out == {"alpha"} | _DISCOVERY   # broad base (shell/read_file) dropped


def test_restrict_to_drops_unknown_names() -> None:
    """Names not in the catalog are silently dropped (intersection with live catalog)."""
    r = _registry_with(["tool_search", "tool_describe", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset({"alpha", "ghost"})))
    assert out == {"alpha"} | _DISCOVERY


def test_restrict_to_openai_protocol() -> None:
    """restrict_to works for the openai protocol shape too."""
    r = _registry_with(["tool_search", "tool_describe", "shell", "alpha"])
    out = _present_names(r.to_provider_schema("openai", restrict_to=frozenset({"alpha"})))
    assert out == {"alpha"} | _DISCOVERY


def test_restrict_to_discovery_already_in_restrict_no_duplicate() -> None:
    """If restrict_to explicitly includes a discovery tool, it still appears exactly once."""
    r = _registry_with(["tool_search", "tool_describe", "alpha"])
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset({"alpha", "tool_search"})))
    # tool_search appears once; tool_describe always present; alpha present
    assert out == {"alpha", "tool_search", "tool_describe"}


def test_restrict_to_missing_discovery_tools_still_safe() -> None:
    """If the catalog lacks a discovery tool, restrict_to degrades gracefully (no KeyError)."""
    r = _registry_with(["shell", "alpha"])  # no tool_search / tool_describe
    out = _present_names(r.to_provider_schema("anthropic", restrict_to=frozenset({"alpha"})))
    # discovery tools not in catalog — they are silently absent (intersection)
    assert "alpha" in out
    assert "shell" not in out  # base dropped under restrict_to
