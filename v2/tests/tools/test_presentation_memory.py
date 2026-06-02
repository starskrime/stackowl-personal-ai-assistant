"""P1-2 — the `memory` tool is always-on (non-evictable base).

An owl whose capability_profile EXCLUDES the "knowledge" group and that does NOT
pin memory must STILL see the `memory` tool, because memory now lives in the
guaranteed base set. This makes the charter's persistent-memory principle
actionable for every owl, regardless of profile.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import (
    _DEFAULT_BASE,
    ToolPresentation,
)
from stackowl.tools.base import Tool, ToolManifest, ToolResult


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


def _names(tools: list[Tool]) -> set[str]:
    return {t.name for t in tools}


def test_memory_is_in_default_base() -> None:
    """`memory` is a non-evictable base tool by default policy."""
    assert "memory" in _DEFAULT_BASE


def test_memory_presented_even_when_profile_excludes_knowledge() -> None:
    """An owl profile that excludes "knowledge" and does NOT pin memory still
    gets `memory` — it is now in the guaranteed (non-evictable) base."""
    catalog = [
        _T("memory", group="knowledge"),
        _T("compile", group="code"),
    ]
    # Default presentation policy (real _DEFAULT_BASE), profile excludes knowledge,
    # no pin of memory.
    selected = ToolPresentation().select(
        all_tools=catalog, profile=["code"], pins=None, hydrated=None
    )
    assert "memory" in _names(selected)
    assert "compile" in _names(selected)  # its actual profile group still flows


def test_memory_survives_cap_with_full_group() -> None:
    """Even under a saturated profile group, `memory` (base) is never evicted."""
    catalog = [_T("memory", group="knowledge")]
    catalog += [_T(f"g{i}", group="code") for i in range(200)]
    selected = ToolPresentation().select(
        all_tools=catalog, profile=["code"], pins=None, hydrated=None
    )
    assert "memory" in _names(selected)
