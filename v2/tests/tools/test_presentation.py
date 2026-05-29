"""E1-S4 — ToolPresentation: DNA-gated presented-set selection + ~25 cap.

Presented set = always-present (tool_search/describe) ∪ guaranteed base ∪ owl
pins ∪ profile-group tools ∪ hydrated(searched) tools, capped at 25 with a
deterministic priority order. Base/always are never hidden by the cap; overflow
stays reachable only via tool_search. Empty profile self-heals to the base set.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import ToolPresentation
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


_BASE = frozenset({"read_file", "write_file", "shell", "web_fetch"})
_ALWAYS = frozenset({"tool_search", "tool_describe"})


def _presenter(cap: int = 25) -> ToolPresentation:
    from stackowl.tools._infra.presentation import PresentationConfig

    return ToolPresentation(PresentationConfig(cap=cap, base_tools=_BASE, always_present=_ALWAYS))


def _names(tools: list[Tool]) -> set[str]:
    return {t.name for t in tools}


def _catalog(*extra: Tool) -> list[Tool]:
    base = [_T(n) for n in (_BASE | _ALWAYS)]
    return base + list(extra)


def test_empty_profile_self_heals_to_base() -> None:
    selected = _presenter().select(all_tools=_catalog(), profile=None, pins=None, hydrated=None)
    assert _names(selected) == _BASE | _ALWAYS  # never zero, always base+meta


def test_always_present_included_even_if_profile_excludes() -> None:
    selected = _presenter().select(all_tools=_catalog(_T("x", group="code")), profile=[], pins=None, hydrated=None)
    assert _names(selected) >= _ALWAYS


def test_profile_group_tools_are_presented() -> None:
    cat = _catalog(_T("compile", group="code"), _T("render", group="media"))
    selected = _presenter().select(all_tools=cat, profile=["code"], pins=None, hydrated=None)
    names = _names(selected)
    assert "compile" in names  # code group in profile
    assert "render" not in names  # media group not in profile


def test_owl_pins_are_presented_regardless_of_group() -> None:
    cat = _catalog(_T("render", group="media"))
    selected = _presenter().select(all_tools=cat, profile=["code"], pins=["render"], hydrated=None)
    assert "render" in _names(selected)


def test_hydrated_searched_tool_is_presented() -> None:
    cat = _catalog(_T("render", group="media"))
    selected = _presenter().select(all_tools=cat, profile=[], pins=None, hydrated={"render"})
    assert "render" in _names(selected)


def test_cap_is_enforced_but_never_hides_base_or_always() -> None:
    # 100 group tools + base + always; cap 25
    group_tools = [_T(f"g{i}", group="code") for i in range(100)]
    selected = _presenter(cap=25).select(all_tools=_catalog(*group_tools), profile=["code"], pins=None, hydrated=None)
    names = _names(selected)
    assert len(selected) <= 25
    assert names >= _BASE and names >= _ALWAYS  # base + always never evicted


def test_selection_is_deterministic() -> None:
    group_tools = [_T(f"g{i}", group="code") for i in range(100)]
    cat = _catalog(*group_tools)
    r1 = [t.name for t in _presenter().select(all_tools=cat, profile=["code"], pins=None, hydrated=None)]
    r2 = [t.name for t in _presenter().select(all_tools=cat, profile=["code"], pins=None, hydrated=None)]
    assert r1 == r2


def test_pins_priority_over_group_tools_under_cap() -> None:
    # cap small so only a few discretionary tools fit; a pin must win over group tools
    group_tools = [_T(f"g{i}", group="code") for i in range(50)]
    cat = _catalog(_T("pinned", group="media"), *group_tools)
    selected = _presenter(cap=len(_BASE | _ALWAYS) + 1).select(
        all_tools=cat, profile=["code"], pins=["pinned"], hydrated=None
    )
    assert "pinned" in _names(selected)  # the one discretionary slot goes to the pin


def test_corrupt_profile_entry_is_skipped_not_crash() -> None:
    # a profile group that matches nothing just contributes no tools (no raise)
    selected = _presenter().select(all_tools=_catalog(), profile=["nonexistent_group"], pins=None, hydrated=None)
    assert _names(selected) >= _BASE | _ALWAYS


# --------------------------------------------------------------------------- #
# integration: ToolRegistry.to_provider_schema(profile=...) gating + cap
# --------------------------------------------------------------------------- #
def test_to_provider_schema_no_profile_returns_all() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry()
    for n in ("read_file", "tool_search", "a", "b"):
        reg.register(_T(n))
    schemas = reg.to_provider_schema("anthropic")  # no profile → all
    assert {s["name"] for s in schemas} == {"read_file", "tool_search", "a", "b"}


def test_to_provider_schema_with_profile_gates_and_caps() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_T("read_file"))
    reg.register(_T("tool_search"))
    for i in range(100):
        reg.register(_T(f"code{i}", group="code"))
    reg.register(_T("render", group="media"))
    schemas = reg.to_provider_schema("anthropic", profile=["code"])
    names = {s["name"] for s in schemas}
    assert len(schemas) <= 25
    assert "read_file" in names and "tool_search" in names  # base/always present
    assert "render" not in names  # media group excluded


def test_register_rejects_dangerous_category_without_consequential() -> None:
    import pytest

    from stackowl.exceptions import ToolRegistrationError
    from stackowl.tools.registry import ToolRegistry

    class _Lock(_T):
        @property
        def manifest(self) -> ToolManifest:
            return ToolManifest(
                name="ha_lock", description="d", parameters=self.parameters,
                action_severity="read", consent_category="lock",
            )

    reg = ToolRegistry()
    with pytest.raises(ToolRegistrationError):
        reg.register(_Lock("ha_lock"))


def test_register_allows_dangerous_category_when_consequential() -> None:
    from stackowl.tools.registry import ToolRegistry

    class _Lock(_T):
        @property
        def manifest(self) -> ToolManifest:
            return ToolManifest(
                name="ha_lock", description="d", parameters=self.parameters,
                action_severity="consequential", consent_category="lock",
            )

    reg = ToolRegistry()
    reg.register(_Lock("ha_lock"))
    assert reg.get("ha_lock") is not None
