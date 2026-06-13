"""Task 2 — tool_outcome_ledger wired at the _dispatch record point.

When _dispatch executes a CONSEQUENTIAL tool that FAILS, the turn-scoped
tool_outcome_ledger must record exactly that outcome so consequential_tally()
returns (cons_f >= 1, cons_s == 0).

Drives the REAL _dispatch (via _run_with_tools) using the identical harness
from tests/pipeline/test_substitution_records_recovery.py:
  - _CapabilityTool / _FakeRegistry / _build_real_dispatch
The tool is registered with action_severity="consequential" and succeed=False.

To block substitution (so the dispatch path is the failure path, not recovery),
the tool has capability_tag=None — no sibling can be found.
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.infra import tool_outcome_ledger as tol
from stackowl.tools.base import Tool, ToolManifest, ToolResult


# ---------------------------------------------------------------------------
# Minimal fake tool — mirrors _CapabilityTool from test_substitution_records_recovery
# ---------------------------------------------------------------------------

class _CapabilityTool(Tool):
    """Controllable severity + success tool for dispatch tests."""

    def __init__(
        self,
        name: str,
        *,
        severity: str,
        capability_tag: str | None,
        output: str,
        succeed: bool,
        params: dict[str, object],
    ) -> None:
        self._name = name
        self._severity = severity
        self._tag = capability_tag
        self._output = output
        self._succeed = succeed
        self._params = params
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"tool: {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return self._params

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self._params,
            action_severity=self._severity,  # type: ignore[arg-type]
            capability_tag=self._tag,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._succeed:
            return ToolResult(success=True, output=self._output, error=None, duration_ms=1.0)
        return ToolResult(
            success=False, output="", error="tool failed", duration_ms=1.0
        )


class _FakeRegistry:
    """Minimal ToolRegistry stand-in."""

    def __init__(self, tools: list[_CapabilityTool]) -> None:
        self._by_name = {t.name: t for t in tools}

    def get(self, name: str) -> _CapabilityTool | None:
        return self._by_name.get(name)

    def all(self) -> list[_CapabilityTool]:
        return list(self._by_name.values())

    def to_provider_schema(self, protocol: str, **_kw: object) -> list[dict[str, object]]:
        return []


async def _build_real_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    reg: _FakeRegistry,
) -> Any:
    """Construct execute._run_with_tools' inner _dispatch with all seams stubbed.

    Identical harness to test_substitution_records_recovery._build_real_dispatch.
    """
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute as exe

    # Stub bounds: everything in-bounds.
    monkeypatch.setattr(
        "stackowl.authz.bounds_guard.check_effective_bounds",
        lambda _effective, _tool_name: None,
    )
    monkeypatch.setattr(
        "stackowl.pipeline.authz_compose.compute_effective_bounds",
        lambda _state, _owl_registry: None,
    )

    class _Gate:
        async def check(self, tool: str, *, channel: Any = None, session_id: Any = None, call_args: Any = None) -> bool:
            return True  # approve all

    class _Services:
        consent_gate = _Gate()

        def __getattr__(self, _name: str) -> None:  # type: ignore[override]
            return None

    monkeypatch.setattr(exe, "get_services", lambda: _Services())

    captured: dict[str, Any] = {}

    class _FakeProvider:
        protocol = "anthropic"

        async def complete_with_tools(self, *, tool_dispatcher: Any, **kw: Any) -> tuple[str, list[Any]]:
            captured["dispatch"] = tool_dispatcher
            return ("", [])

    state = PipelineState(
        input_text="hi", owl_name="secretary", session_id="s1",
        channel="cli", trace_id="t1", pipeline_step="execute",
    )

    await exe._run_with_tools(state, _FakeProvider(), reg)  # type: ignore[arg-type]
    return captured["dispatch"]


# ===========================================================================
# The focused test: a failed consequential dispatch is recorded in the ledger.
# ===========================================================================

@pytest.mark.asyncio
async def test_failed_consequential_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _dispatch executes a CONSEQUENTIAL tool that FAILS, the turn-scoped
    tool_outcome_ledger records (cons_f >= 1, cons_s == 0).

    capability_tag=None blocks any substitution path, so the failure is
    recorded from the primary _dispatch path (not from _try_substitute).
    """
    failing_consequential = _CapabilityTool(
        "dangerous_write",
        severity="consequential",
        capability_tag=None,  # no tag → no substitute found
        output="",
        succeed=False,
        params={"type": "object", "properties": {"payload": {"type": "string"}}},
    )
    reg = _FakeRegistry([failing_consequential])

    token = tol.bind()
    try:
        dispatch = await _build_real_dispatch(monkeypatch, reg)

        # Dispatch the failing consequential tool.
        await dispatch("dangerous_write", {"payload": "test"})

        cons_f, cons_s = tol.consequential_tally()
    finally:
        tol.reset(token)

    assert cons_f >= 1, (
        f"Expected at least 1 consequential failure recorded, got cons_f={cons_f}"
    )
    assert cons_s == 0, (
        f"Expected 0 consequential successes, got cons_s={cons_s}"
    )


@pytest.mark.asyncio
async def test_successful_consequential_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _dispatch executes a CONSEQUENTIAL tool that SUCCEEDS, the ledger
    records (cons_f == 0, cons_s >= 1).
    """
    succeeding_consequential = _CapabilityTool(
        "safe_write",
        severity="consequential",
        capability_tag=None,
        output="wrote it",
        succeed=True,
        params={"type": "object", "properties": {"payload": {"type": "string"}}},
    )
    reg = _FakeRegistry([succeeding_consequential])

    token = tol.bind()
    try:
        dispatch = await _build_real_dispatch(monkeypatch, reg)
        await dispatch("safe_write", {"payload": "hello"})
        cons_f, cons_s = tol.consequential_tally()
    finally:
        tol.reset(token)

    assert cons_s >= 1, f"Expected >= 1 consequential success, got cons_s={cons_s}"
    assert cons_f == 0, f"Expected 0 consequential failures, got cons_f={cons_f}"


@pytest.mark.asyncio
async def test_read_tool_not_in_consequential_tally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A READ-severity tool outcome is recorded but does NOT count in the
    consequential tally (the ledger still records it — get_outcomes returns it).
    """
    read_tool = _CapabilityTool(
        "read_file",
        severity="read",
        capability_tag=None,
        output="file contents",
        succeed=True,
        params={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    reg = _FakeRegistry([read_tool])

    token = tol.bind()
    try:
        dispatch = await _build_real_dispatch(monkeypatch, reg)
        await dispatch("read_file", {"path": "/tmp/x"})
        cons_f, cons_s = tol.consequential_tally()
        outcomes = tol.get_outcomes()
    finally:
        tol.reset(token)

    # The outcome IS recorded (get_outcomes has it).
    assert any(o.name == "read_file" for o in outcomes), (
        f"Expected read_file in outcomes, got: {outcomes!r}"
    )
    # But it does NOT appear in the CONSEQUENTIAL tally.
    assert cons_f == 0 and cons_s == 0, (
        f"Read tool must not appear in consequential tally; got cons_f={cons_f}, cons_s={cons_s}"
    )
