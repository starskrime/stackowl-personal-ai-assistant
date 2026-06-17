"""SEC-4 / F046 — a learned CONSEQUENTIAL tool carries a fixed consent_category.

Shadow-guard parity: a learned consequential tool must be treated by the registry
exactly like a native consequential/consent-category tool — both as
``_is_dangerous`` AND carrying a (non-author-controlled) consent_category so the
consent surface keys off the same field. The author can NEVER mint the category
(mirrors how toolset_group is pinned to "learned").
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import ToolRegistrationError
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.meta.learned_shell_tool import LearnedShellTool
from stackowl.tools.meta.tool_spec import LearnedToolSpec, ToolParam
from stackowl.tools.registry import ToolRegistry


def _spec(severity: str) -> LearnedToolSpec:
    return LearnedToolSpec(
        name="learned_thing",
        description="A learned shell-backed tool for tests.",
        params=[ToolParam(name="x", type="string", description="d", required=True)],
        argv_template=["echo", "{x}"],
        action_severity=severity,  # type: ignore[arg-type]
    )


def test_consequential_learned_tool_carries_consent_category() -> None:
    tool = LearnedShellTool(_spec("consequential"))
    m = tool.manifest
    assert m.action_severity == "consequential"
    # Parity: a consent_category is present (not author-chosen) so the dangerous
    # shadow-guard and consent surface key off the same field as native tools.
    assert m.consent_category is not None


def test_non_consequential_learned_tool_has_no_forced_category() -> None:
    tool = LearnedShellTool(_spec("read"))
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.consent_category is None


def test_registry_treats_learned_consequential_as_dangerous() -> None:
    reg = ToolRegistry()
    tool = LearnedShellTool(_spec("consequential"))
    # _is_dangerous parity with a native consequential tool.
    assert reg._is_dangerous(tool) is True


def test_learned_consequential_cannot_shadow_native_tool() -> None:
    """A learned consequential tool may not silently clobber a registered name."""

    class _Native(Tool):
        @property
        def name(self) -> str:
            return "learned_thing"

        @property
        def description(self) -> str:
            return "native"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True, output="", duration_ms=0)

    reg = ToolRegistry()
    reg.register(_Native())
    with pytest.raises(ToolRegistrationError):
        reg.register(LearnedShellTool(_spec("consequential")), replace=True)


def test_learned_consequential_registers_when_name_is_free() -> None:
    reg = ToolRegistry()
    reg.register(LearnedShellTool(_spec("consequential")))
    assert reg.get("learned_thing") is not None
