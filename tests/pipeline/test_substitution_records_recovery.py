"""W3.T14 + recovery_context — capability substitution records a recovery event.

When ``_try_substitute`` successfully routes around a failing primary to a
working read sibling, it must record a ``RecoveryEvent`` in the turn-scoped
``recovery_context`` so downstream (render step, per-turn log) can surface it
to the user.

Drives ``_dispatch`` (the real substitution actuator path) with a failing
primary + a working same-tag read sibling, asserts:
  - ``recovery_context.get_recovery()`` has exactly one event
  - event.kind == "substitution"
  - event.failed == primary name
  - event.recovered_via == sibling name
  - event.user_visible is True

Mirrors the harness from ``tests/pipeline/test_dispatch_substitution.py``
(``_build_real_dispatch``).
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.infra import recovery_context
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# ---------------------------------------------------------------------------
# Minimal fake tool — mirrors _CapabilityTool from the substitution journey and
# _FakeTool from test_dispatch_substitution; using the REAL adapter names so the
# declarative web_knowledge adapters in capability_substitution apply.
# ---------------------------------------------------------------------------

class _CapabilityTool(Tool):
    """Web_knowledge-class tool with controllable severity + success."""

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
        return f"web_knowledge capability: {self._name}"

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
            success=False, output="", error="capability unavailable", duration_ms=1.0
        )


class _FakeRegistry:
    """Minimal ToolRegistry stand-in exposing get()/all()/to_provider_schema()."""

    def __init__(self, tools: list[_CapabilityTool]) -> None:
        self._by_name = {t.name: t for t in tools}

    def get(self, name: str) -> _CapabilityTool | None:
        return self._by_name.get(name)

    def all(self) -> list[_CapabilityTool]:
        return list(self._by_name.values())

    def to_provider_schema(self, protocol: str, **_kw: object) -> list[dict[str, object]]:
        return []


def _make_registry(*, primary_name: str, sibling_name: str) -> _FakeRegistry:
    """Failing consequential primary + working read sibling, same web_knowledge tag."""
    primary = _CapabilityTool(
        primary_name,
        severity="consequential",
        capability_tag="web_knowledge",
        output="PRIMARY_OK",
        succeed=False,
        params={"type": "object", "properties": {"task": {"type": "string"}}},
    )
    sibling = _CapabilityTool(
        sibling_name,
        severity="read",
        capability_tag="web_knowledge",
        output="SIBLING_OK",
        succeed=True,
        params={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    return _FakeRegistry([primary, sibling])


# ---------------------------------------------------------------------------
# Shared helper: build the real _dispatch closure (same pattern as
# test_dispatch_substitution._build_real_dispatch).
# ---------------------------------------------------------------------------

async def _build_real_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    reg: _FakeRegistry,
) -> Any:
    """Construct execute._run_with_tools' inner _dispatch with all seams stubbed."""
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
            return True  # approve all → the primary runs and fails

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
# The focused test: substitution records exactly one recovery event.
# ===========================================================================

@pytest.mark.asyncio
async def test_substitution_records_recovery_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a successful capability substitution, _try_substitute records exactly
    one RecoveryEvent(kind='substitution', failed=primary, recovered_via=sibling,
    user_visible=True) in the turn-scoped recovery_context.

    Uses the REAL adapter names (browser_browse → web_search) so the declarative
    web_knowledge adapters apply; drives _dispatch (the real actuator path)."""
    reg = _make_registry(primary_name="browser_browse", sibling_name="web_search")

    # Bind a fresh recovery context for this turn.
    token = recovery_context.bind()
    try:
        dispatch = await _build_real_dispatch(monkeypatch, reg)

        # browser_browse fails → actuator routes to web_search (read sibling).
        result = await dispatch("browser_browse", {"task": "weather today"})

        events = recovery_context.get_recovery()
    finally:
        recovery_context.reset(token)

    # The substitution succeeded — sibling output in observation.
    assert "SIBLING_OK" in result, (
        f"Substitution did not succeed — actuator may not have run; result: {result!r}"
    )

    # Exactly one recovery event recorded.
    assert len(events) == 1, (
        f"Expected exactly 1 recovery event, got {len(events)}: {events!r}"
    )

    ev = events[0]
    assert ev.kind == "substitution", f"kind mismatch: {ev.kind!r}"
    assert ev.failed == "browser_browse", f"failed mismatch: {ev.failed!r}"
    assert ev.recovered_via == "web_search", f"recovered_via mismatch: {ev.recovered_via!r}"
    assert ev.user_visible is True, f"user_visible mismatch: {ev.user_visible!r}"


@pytest.mark.asyncio
async def test_substitution_no_recovery_event_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When substitution finds NO eligible sibling (falls through to TOOL_FAILED),
    NO recovery event is recorded — only successful substitutions are captured."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    # Only a consequential sibling → substitution is blocked → no capture.
    cons_sibling = _CapabilityTool(
        "browser_browse_alt",
        severity="consequential",
        capability_tag="web_knowledge",
        output="SHOULD_NOT_RUN",
        succeed=True,
        params={"type": "object", "properties": {"task": {"type": "string"}}},
    )
    primary = _CapabilityTool(
        "browser_browse",
        severity="consequential",
        capability_tag="web_knowledge",
        output="PRIMARY_OK",
        succeed=False,
        params={"type": "object", "properties": {"task": {"type": "string"}}},
    )
    reg = _FakeRegistry([primary, cons_sibling])

    token = recovery_context.bind()
    try:
        dispatch = await _build_real_dispatch(monkeypatch, reg)
        result = await dispatch("browser_browse", {"task": "x"})
        events = recovery_context.get_recovery()
    finally:
        recovery_context.reset(token)

    assert result.startswith(TOOL_FAILED_MARKER), (
        f"Expected TOOL_FAILED fall-through, got: {result!r}"
    )
    assert len(events) == 0, (
        f"No recovery event expected when substitution found no eligible sibling, got: {events!r}"
    )
