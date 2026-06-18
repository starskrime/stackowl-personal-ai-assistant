"""E0-S4 — tool registration hardening.

register() asserts unique names by default (raise on collision unless
replace=True) and fails closed when a dangerous-category tool would shadow, or
be shadowed by, an existing registration — so a skill/MCP tool can never
silently clobber a native consequential tool. See E0-S4-registration-hardening.md.
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import ToolRegistrationError
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _Stub(Tool):
    def __init__(self, name: str, severity: str = "read") -> None:
        self._name = name
        self._severity = severity

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "stub"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name, description=self.description,
            parameters=self.parameters, action_severity=self._severity,  # type: ignore[arg-type]
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok", duration_ms=1.0)


def test_distinct_names_register() -> None:
    reg = ToolRegistry()
    reg.register(_Stub("a"))
    reg.register(_Stub("b"))
    assert {t.name for t in reg.all()} == {"a", "b"}


def test_duplicate_name_raises() -> None:
    reg = ToolRegistry()
    reg.register(_Stub("dup"))
    with pytest.raises(ToolRegistrationError):
        reg.register(_Stub("dup"))


def test_replace_true_allows_override_for_safe_tools() -> None:
    reg = ToolRegistry()
    first = _Stub("x")
    reg.register(first)
    second = _Stub("x")
    reg.register(second, replace=True)
    assert reg.get("x") is second


def test_dangerous_incoming_cannot_shadow_existing_even_with_replace() -> None:
    reg = ToolRegistry()
    reg.register(_Stub("shell"))  # native, safe-severity name
    with pytest.raises(ToolRegistrationError):
        reg.register(_Stub("shell", severity="consequential"), replace=True)


def test_cannot_replace_an_existing_dangerous_tool() -> None:
    reg = ToolRegistry()
    reg.register(_Stub("execute_code", severity="consequential"))
    with pytest.raises(ToolRegistrationError):
        reg.register(_Stub("execute_code"), replace=True)


def test_external_source_tool_cannot_clobber_native() -> None:
    reg = ToolRegistry()
    reg.register(_Stub("shell"))  # native
    # an MCP/skill server offering a same-named tool must be rejected
    with pytest.raises(ToolRegistrationError):
        reg.register(_Stub("shell"), source_name="mcp:evil")


def test_replace_updates_source_map() -> None:
    reg = ToolRegistry()
    reg.register(_Stub("t"), source_name="src1")
    reg.register(_Stub("t"), source_name="src2", replace=True)
    # the old source entry must not leave a dangling unregister target
    removed = reg.unregister_by_source("src1")
    assert removed == 0  # 't' is no longer owned by src1
    assert reg.get("t") is not None
    assert reg.unregister_by_source("src2") == 1
