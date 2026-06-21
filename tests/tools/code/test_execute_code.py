"""Unit tests for ExecuteCodeTool (E11-S5).

Covers the tool's contract in isolation (a FAKE selector/backend — no real
sandbox): the structured ToolResult mapping, the python-only refusal, the
NEVER-host-exec safety (no selector / unavailable selector / backend error all
return structured failures and run NOTHING), the per-call consent summary (GAP-A),
and the manifest (consequential, group=code). The live-sandbox + gateway wiring is
proven by the J-E11 journey (``tests/journeys/test_j11_execute_code.py``).
"""

from __future__ import annotations

import json

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.sandbox.base import SandboxAvailability, SandboxBackend
from stackowl.sandbox.selector import SandboxSelector
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps
from stackowl.tools.code.execute_code import ExecuteCodeTool


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    """execute() asserts not-test-mode via Tool.__call__; here we call execute()
    directly, but the fake backends never touch the host so this is belt-only."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _FakeBackend(SandboxBackend):
    """Records the spec it was handed and returns a scripted ExecResult."""

    def __init__(self, *, result: ExecResult, rootless: bool = True) -> None:
        self._result = result
        self._rootless = rootless
        self.ran_spec: ExecSpec | None = None

    @property
    def name(self) -> str:
        return "fake"

    @property
    def is_rootless(self) -> bool:
        return self._rootless

    @property
    def supports_network(self) -> bool:
        return not self._rootless

    async def is_available(self) -> SandboxAvailability:
        return SandboxAvailability.ok()

    async def run(self, spec: ExecSpec, *, ptc_factory: object | None = None) -> ExecResult:
        self.ran_spec = spec
        self.ran_ptc_factory = ptc_factory
        return self._result


class _RaisingBackend(_FakeBackend):
    async def run(self, spec: ExecSpec, *, ptc_factory: object | None = None) -> ExecResult:
        self.ran_spec = spec
        raise RuntimeError("backend exploded")


class _StubProbe:
    """Deterministic probe — drive the selector without touching the host."""

    def __init__(self, *, bwrap: bool, docker: bool) -> None:
        self.bwrap_viable = bwrap
        self.docker_viable = docker
        self.bwrap_reason = "stub bwrap"
        self.docker_reason = "stub docker"


def _ok_result(stdout: str = "4\n") -> ExecResult:
    return ExecResult.ok(
        stdout=stdout, stderr="", exit_code=0, backend_used="fake",
        network_enabled=False, caps_applied=ResourceCaps(), duration_ms=5,
    )


def _services_with(selector: SandboxSelector | None) -> StepServices:
    return StepServices(sandbox_selector=selector)


async def _run(tool: ExecuteCodeTool, services: StepServices, **kwargs):  # noqa: ANN003
    token = set_services(services)
    try:
        return await tool.execute(**kwargs)
    finally:
        reset_services(token)


# --------------------------------------------------------------- manifest


def test_manifest_is_consequential_code_group() -> None:
    m = ExecuteCodeTool().manifest
    assert m.name == "execute_code"
    assert m.action_severity == "consequential"
    assert m.toolset_group == "code"


def test_execute_code_is_on_always_ask_list() -> None:
    from stackowl.tools.consent import _DEFAULT_ALWAYS_ASK_TOOLS

    assert "execute_code" in _DEFAULT_ALWAYS_ASK_TOOLS


def test_execute_code_is_child_excluded() -> None:
    from stackowl.pipeline.steps.execute import _CHILD_EXCLUDED_TOOLS

    assert "execute_code" in _CHILD_EXCLUDED_TOOLS


# --------------------------------------------------------------- consent summary (GAP-A)


def test_consent_summary_shows_code_language_and_network() -> None:
    tool = ExecuteCodeTool()
    summary = tool.consent_summary(code="print(2+2)", language="python", network=False)
    assert summary is not None
    assert "print(2+2)" in summary  # the ACTUAL code is shown
    assert "python" in summary
    assert "no network" in summary


def test_consent_summary_flags_network_request() -> None:
    summary = ExecuteCodeTool().consent_summary(code="x=1", network=True)
    assert summary is not None and "WITH network access" in summary


def test_consent_summary_bounds_long_code() -> None:
    long_code = "\n".join(f"line_{i} = {i}" for i in range(200))
    summary = ExecuteCodeTool().consent_summary(code=long_code)
    assert summary is not None
    assert "code truncated for display" in summary
    assert "line_199" not in summary  # tail dropped


def test_consent_summary_never_raises_on_bad_args() -> None:
    # Missing 'code' / wrong types must not raise — best-effort, never blank-crash.
    summary = ExecuteCodeTool().consent_summary(language=123)
    assert isinstance(summary, str)


# --------------------------------------------------------------- happy path


async def test_run_maps_execresult_to_structured_toolresult() -> None:
    backend = _FakeBackend(result=_ok_result("4\n"))
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    res = await _run(ExecuteCodeTool(), _services_with(selector), code="print(2+2)")

    assert res.success is True
    record = json.loads(res.output)["record"]
    assert record["stdout"] == "4\n"
    assert record["exit_code"] == 0
    assert record["exit_reason"] == "ok"
    assert record["backend"] == "fake"
    assert record["network_enabled"] is False
    assert set(record["caps"]) >= {"mem_mib", "cpu_cores", "pids", "wall_time_s"}
    assert record["truncated"] is False
    # The spec the backend got carries the code + defaults (deny network).
    assert backend.ran_spec is not None
    assert backend.ran_spec.code == "print(2+2)"
    assert backend.ran_spec.network is False


async def test_program_nonzero_exit_is_not_a_tool_failure() -> None:
    # A program that exits 1 still RAN: exit_reason "ok" → tool success, exit_code surfaced.
    backend = _FakeBackend(result=ExecResult.ok(
        stdout="", stderr="boom", exit_code=1, backend_used="fake",
        network_enabled=False, caps_applied=ResourceCaps(), duration_ms=3,
    ))
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    res = await _run(ExecuteCodeTool(), _services_with(selector), code="raise SystemExit(1)")
    assert res.success is True
    assert json.loads(res.output)["record"]["exit_code"] == 1


# --------------------------------------------------------------- safety: NEVER host exec


async def test_no_selector_wired_returns_unavailable_runs_nothing() -> None:
    res = await _run(ExecuteCodeTool(), _services_with(None), code="print(1)")
    assert res.success is False
    assert "unavailable" in (res.error or "")
    assert "host" in (res.error or "")
    # Never-ran refusal — not an effectful failure (must not trip the give-up floor).
    assert res.side_effect_committed is False


async def test_selector_unavailable_returns_unavailable_runs_nothing() -> None:
    # Neither backend viable → the real selector returns a structured unavailable.
    selector = SandboxSelector([], probe=_StubProbe(bwrap=False, docker=False))
    res = await _run(ExecuteCodeTool(), _services_with(selector), code="print(1)")
    assert res.success is False
    assert "unavailable" in (res.error or "")


async def test_backend_run_raising_degrades_structured_no_host_exec() -> None:
    backend = _RaisingBackend(result=_ok_result())
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    res = await _run(ExecuteCodeTool(), _services_with(selector), code="print(1)")
    assert res.success is False
    assert "host" in (res.error or "")
    assert backend.ran_spec is not None  # it reached the backend, which then raised
    # Positive control: code may have started in the sandbox → committed stays True.
    assert res.side_effect_committed is True


# --------------------------------------------------------------- python-only


async def test_non_python_language_is_refused() -> None:
    backend = _FakeBackend(result=_ok_result())
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    res = await _run(
        ExecuteCodeTool(), _services_with(selector), code="console.log(1)", language="javascript"
    )
    assert res.success is False
    assert "not supported" in (res.error or "")
    assert backend.ran_spec is None  # never reached a backend
    assert res.side_effect_committed is False  # never-ran refusal


async def test_invalid_args_are_refused() -> None:
    res = await _run(ExecuteCodeTool(), _services_with(None), code="x", bogus=True)
    assert res.success is False
    assert "invalid arguments" in (res.error or "")
    assert res.side_effect_committed is False  # pre-exec refusal


# --------------------------------------------------------------- governor (E11-S6)


from contextlib import asynccontextmanager  # noqa: E402

from stackowl.sandbox.governor import SandboxGovernor, SandboxSaturatedError  # noqa: E402


class _RefusingGovernor:
    """A governor double whose slot() always REFUSES (saturated), like the real one
    past its bounded wait. Never invokes the backend."""

    @asynccontextmanager
    async def slot(self, timeout=None):  # noqa: ANN001, ANN202
        raise SandboxSaturatedError("saturated (test)")
        yield  # pragma: no cover — unreachable, satisfies the cm protocol


def _services_with_gov(selector, governor):  # noqa: ANN001, ANN202
    return StepServices(sandbox_selector=selector, sandbox_governor=governor)


async def test_governor_saturated_refuses_and_never_runs_backend() -> None:
    backend = _FakeBackend(result=_ok_result())
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    res = await _run(
        ExecuteCodeTool(),
        _services_with_gov(selector, _RefusingGovernor()),
        code="print(2+2)",
    )
    assert res.success is False
    assert "too many code executions" in (res.error or "")
    assert backend.ran_spec is None  # the backend was NEVER reached — nothing ran
    assert res.side_effect_committed is False  # nothing ran → not effectful


async def test_governor_with_a_free_slot_runs_normally() -> None:
    backend = _FakeBackend(result=_ok_result("4\n"))
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    res = await _run(
        ExecuteCodeTool(),
        _services_with_gov(selector, SandboxGovernor(2)),
        code="print(2+2)",
    )
    assert res.success is True
    assert backend.ran_spec is not None  # acquired a slot and ran


async def test_governor_none_runs_normally_backward_compat() -> None:
    backend = _FakeBackend(result=_ok_result("4\n"))
    selector = SandboxSelector([backend], probe=_StubProbe(bwrap=True, docker=False))  # type: ignore[arg-type]
    # No governor wired (the default) → runs ungated, as before.
    res = await _run(ExecuteCodeTool(), _services_with(selector), code="print(2+2)")
    assert res.success is True
    assert backend.ran_spec is not None
