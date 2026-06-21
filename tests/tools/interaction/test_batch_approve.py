"""Tests for :class:`BatchApproveTool` — batch-consent UX (J8).

Covers: validate (empty/over-cap/unknown tool → structured, no execution);
non-interactive → fail closed (no execution, structured needs-human); approve-all
→ every action executes + the batch grant + each action are audited; reject →
NONE execute (rejection audited); a failing action → structured partial (the
others still run, never raises); manifest severity/group; registered in
with_defaults.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.interaction.batch_approve import _MAX_ACTIONS, BatchApproveTool
from stackowl.tools.registry import ToolRegistry


class _FakeAdapter:
    """Captures send_clarify so we can prove exactly ONE batch prompt was sent."""

    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.calls: list[tuple[str, str, tuple[str, ...], str]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_clarify(
        self, session_id: str, question: str, choices: tuple[str, ...], clarify_id: str,
    ) -> None:
        self.calls.append((session_id, question, tuple(choices), clarify_id))


class _RecordingTool(Tool):
    """A real registered tool that records each execution (the batch's side-effect)."""

    def __init__(self, name: str, *, fail: bool = False, raises: bool = False) -> None:
        self._name = name
        self._fail = fail
        self._raises = raises
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"recording tool {self._name}"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self._name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._raises:
            raise RuntimeError(f"{self._name} blew up")
        if self._fail:
            return ToolResult(success=False, output="", error=f"{self._name} failed", duration_ms=0.0)
        return ToolResult(success=True, output=f"{self._name} ran", duration_ms=0.0)


class _RecordingAudit:
    """Mirrors AuditLogger.append; records the audited batch window."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append(self, event_type: str, actor: str, target: str | None, details: dict[str, object]) -> None:
        self.rows.append({"event_type": event_type, "actor": actor, "target": target, "details": details})


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


_Env = tuple[
    ClarifyGateway, ToolRegistry, _RecordingAudit,
    _RecordingTool, _RecordingTool, _RecordingTool,
]


@pytest.fixture
def env() -> Iterator[_Env]:
    gw = ClarifyGateway()
    gw.register_adapter("cli", _FakeAdapter("cli"))  # type: ignore[arg-type]
    a, b, c = _RecordingTool("act_a"), _RecordingTool("act_b"), _RecordingTool("act_c")
    reg = _registry(a, b, c)
    audit = _RecordingAudit()
    token = set_services(
        StepServices(clarify_gateway=gw, tool_registry=reg, audit_logger=audit)  # type: ignore[arg-type]
    )
    try:
        yield gw, reg, audit, a, b, c
    finally:
        reset_services(token)


_PLAN = [
    {"tool": "act_a", "args": {"x": 1}, "summary": "do A"},
    {"tool": "act_b", "args": {}, "summary": "do B"},
    {"tool": "act_c", "args": {}, "summary": "do C"},
]


def _adapter(gw: ClarifyGateway) -> _FakeAdapter:
    ad = gw._adapters["cli"]
    assert isinstance(ad, _FakeAdapter)
    return ad


# --------------------------------------------------------------- approve-all


async def test_approve_all_executes_every_action_and_audits(env) -> None:  # noqa: ANN001
    gw, _reg, audit, a, b, c = env
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        task = asyncio.ensure_future(
            BatchApproveTool().execute(intro="Morning routine", actions=_PLAN)
        )
        await asyncio.sleep(0)  # let it register + park on the waiter
        adapter = _adapter(gw)
        # EXACTLY ONE batch prompt (one keyboard) listing all three actions.
        assert len(adapter.calls) == 1
        _sid, question, choices, _cid = adapter.calls[0]
        assert choices == ("Approve all", "Reject")
        assert "do A" in question and "do B" in question and "do C" in question
        # The user taps "Approve all".
        assert gw.try_resolve("s1", "cli", "Approve all") is not None
        result = await task
    finally:
        TraceContext.reset(trace)

    assert result.success is True
    # Positive control: an executed batch reports success=True via _ok (committed True).
    assert result.side_effect_committed is True
    # All three actions EXECUTED with their args.
    assert a.calls == [{"x": 1}] and b.calls == [{}] and c.calls == [{}]
    assert "3 succeeded" in result.output
    # Audited: the batch grant + one row per action.
    grants = [r for r in audit.rows if r["event_type"] == "batch_approval.granted"]
    actions = [r for r in audit.rows if r["event_type"] == "batch_approval.action"]
    assert len(grants) == 1 and len(actions) == 3
    assert all(r["details"]["success"] is True for r in actions)


# ------------------------------------------------------------------- reject


async def test_reject_executes_nothing_and_audits_rejection(env) -> None:  # noqa: ANN001
    gw, _reg, audit, a, b, c = env
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        task = asyncio.ensure_future(
            BatchApproveTool().execute(intro="Morning routine", actions=_PLAN)
        )
        await asyncio.sleep(0)
        assert gw.try_resolve("s1", "cli", "Reject") is not None
        result = await task
    finally:
        TraceContext.reset(trace)

    assert result.success is True
    assert a.calls == [] and b.calls == [] and c.calls == []  # NOTHING ran
    assert [r["event_type"] for r in audit.rows] == ["batch_approval.rejected"]
    assert not any(r["event_type"] == "batch_approval.granted" for r in audit.rows)


async def test_timeout_executes_nothing(env) -> None:  # noqa: ANN001
    gw, _reg, audit, a, b, c = env
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        # No tap → bounded timeout → nothing runs (rejection audited).
        result = await BatchApproveTool(timeout_s=0.05).execute(
            intro="Morning routine", actions=_PLAN
        )
    finally:
        TraceContext.reset(trace)
    assert result.success is True
    assert a.calls == [] and b.calls == [] and c.calls == []
    assert any(r["event_type"] == "batch_approval.rejected" for r in audit.rows)


# ----------------------------------------------------- partial / self-healing


async def test_failing_action_surfaced_others_still_run(env) -> None:  # noqa: ANN001
    """A failing action → structured partial; the others still run; never raises."""
    gw, reg, audit, a, _b, c = env
    # Middle action raises; the batch must catch it, surface it, and run a + c.
    boom = _RecordingTool("act_boom", raises=True)
    reg.register(boom)
    plan = [
        {"tool": "act_a", "args": {}, "summary": "do A"},
        {"tool": "act_boom", "args": {}, "summary": "do boom"},
        {"tool": "act_c", "args": {}, "summary": "do C"},
    ]
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        task = asyncio.ensure_future(
            BatchApproveTool().execute(intro="Routine", actions=plan)
        )
        await asyncio.sleep(0)
        assert gw.try_resolve("s1", "cli", "Approve all") is not None
        result = await task  # must NOT raise
    finally:
        TraceContext.reset(trace)

    assert result.success is True
    assert a.calls == [{}] and c.calls == [{}]  # others STILL ran
    assert boom.calls == [{}]  # the failing one was attempted
    assert "2 succeeded, 1 failed" in result.output
    assert "FAILED" in result.output
    # The failed action is audited with success=False (never masked).
    failed = [
        r for r in audit.rows
        if r["event_type"] == "batch_approval.action" and r["details"]["success"] is False
    ]
    assert len(failed) == 1 and failed[0]["details"]["tool"] == "act_boom"


async def test_failing_result_action_surfaced(env) -> None:  # noqa: ANN001
    """An action returning success=False (not raising) is surfaced partial too."""
    gw, reg, audit, a, _b, c = env
    flop = _RecordingTool("act_flop", fail=True)
    reg.register(flop)
    plan = [
        {"tool": "act_a", "args": {}, "summary": "do A"},
        {"tool": "act_flop", "args": {}, "summary": "do flop"},
    ]
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        task = asyncio.ensure_future(
            BatchApproveTool().execute(intro="Routine", actions=plan)
        )
        await asyncio.sleep(0)
        assert gw.try_resolve("s1", "cli", "Approve all") is not None
        result = await task
    finally:
        TraceContext.reset(trace)
    assert result.success is True
    assert a.calls == [{}] and flop.calls == [{}]
    assert "1 succeeded, 1 failed" in result.output


# --------------------------------------------------------------- non-interactive


async def test_non_interactive_fail_closed_no_execution(env) -> None:  # noqa: ANN001
    gw, _reg, audit, a, b, c = env
    trace = TraceContext.start(session_id="s1", interactive=False, channel="cli")
    try:
        result = await BatchApproveTool().execute(intro="Routine", actions=_PLAN)
    finally:
        TraceContext.reset(trace)
    assert result.success is True
    assert "non-interactive" in result.output.lower()
    # Nothing executed, nothing parked, nothing granted.
    assert a.calls == [] and b.calls == [] and c.calls == []
    assert len(_adapter(gw).calls) == 0
    assert gw.try_resolve("s1", "cli", "x") is None
    assert not any(r["event_type"] == "batch_approval.granted" for r in audit.rows)


# ----------------------------------------------------------------- validation


async def test_unknown_tool_is_structured_no_prompt(env) -> None:  # noqa: ANN001
    gw, _reg, _audit, a, _b, _c = env
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await BatchApproveTool().execute(
            intro="Routine",
            actions=[{"tool": "does_not_exist", "args": {}, "summary": "ghost"}],
        )
    finally:
        TraceContext.reset(trace)
    assert result.success is False
    assert "unknown tool" in (result.error or "").lower()
    # Pre-execution refusal (before any action runs) → not an effectful failure.
    assert result.side_effect_committed is False
    # No prompt was ever sent (we reject before asking the user).
    assert len(_adapter(gw).calls) == 0
    assert a.calls == []


async def test_prompt_surfaces_the_real_tool_not_just_summary(env) -> None:  # noqa: ANN001
    """CONSENT INTEGRITY: the batch prompt must show the TRUSTED tool name, so a
    misleading model-authored summary cannot hide which tool actually executes."""
    gw, _reg, _audit, *_ = env
    # A deliberately misleading summary paired with a real tool.
    misleading = [{"tool": "act_a", "args": {"x": 1}, "summary": "send a friendly reminder"}]
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        task = asyncio.ensure_future(
            BatchApproveTool().execute(intro="Routine", actions=misleading)
        )
        await asyncio.sleep(0)
        _sid, question, _choices, _cid = _adapter(gw).calls[0]
        # The REAL tool name is on the consent surface — not just the summary.
        assert "act_a" in question, question
        assert "send a friendly reminder" in question  # summary still shown too
        gw.try_resolve("s1", "cli", "Reject")
        await task
    finally:
        TraceContext.reset(trace)


async def test_batch_refuses_to_nest_a_consent_tool(env) -> None:  # noqa: ANN001
    """The batch is the single consent surface — nesting batch_approve (recursion)
    or clarify (a second prompt inside an approved batch) is refused, no prompt."""
    gw, reg, _audit, *_ = env
    # Register a tool named 'batch_approve' so the NESTED check (not unknown) fires.
    reg.register(_RecordingTool("batch_approve"))
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await BatchApproveTool().execute(
            intro="Routine",
            actions=[{"tool": "batch_approve", "args": {}, "summary": "approve more"}],
        )
    finally:
        TraceContext.reset(trace)
    assert result.success is False
    assert "cannot run" in (result.error or "").lower()
    assert len(_adapter(gw).calls) == 0  # refused BEFORE any prompt


async def test_empty_actions_is_structured(env) -> None:  # noqa: ANN001
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await BatchApproveTool().execute(intro="Routine", actions=[])
    finally:
        TraceContext.reset(trace)
    assert result.success is False
    assert "invalid plan" in (result.error or "").lower()
    assert result.side_effect_committed is False  # pre-exec refusal, nothing ran


async def test_over_cap_is_structured(env) -> None:  # noqa: ANN001
    over = [{"tool": "act_a", "args": {}, "summary": f"s{i}"} for i in range(_MAX_ACTIONS + 1)]
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await BatchApproveTool().execute(intro="Routine", actions=over)
    finally:
        TraceContext.reset(trace)
    assert result.success is False
    assert "invalid plan" in (result.error or "").lower()


async def test_missing_intro_is_structured(env) -> None:  # noqa: ANN001
    trace = TraceContext.start(session_id="s1", interactive=True, channel="cli")
    try:
        result = await BatchApproveTool().execute(actions=_PLAN)
    finally:
        TraceContext.reset(trace)
    assert result.success is False


# ----------------------------------------------------------------- manifest


def test_manifest_severity_and_group() -> None:
    m = BatchApproveTool().manifest
    assert m.name == "batch_approve"
    # write (NOT consequential) — the batch presentation IS the consent, so the
    # per-action dispatch gate must NOT double-prompt.
    assert m.action_severity == "write"
    assert m.toolset_group == "interaction"


def test_registered_in_with_defaults() -> None:
    registry = ToolRegistry.with_defaults()
    assert any(t.name == "batch_approve" for t in registry.all())
