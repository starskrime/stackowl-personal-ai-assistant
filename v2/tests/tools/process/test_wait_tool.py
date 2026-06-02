"""Tool-level tests for the ``wait`` tool (E9-S2).

Drive the TOOL surface (not the registry directly), with a REAL
:class:`ProcessRegistry` injected via ``set_services`` and a ``TraceContext``
session id (mirroring ``test_process_tool.py``). Subprocesses use ``sys.executable
-c "..."`` so the suite runs identically on Windows and POSIX
([[feedback_cross_platform]]). ``STACKOWL_HOME`` is pointed at a tmp dir so the
registry checkpoint never touches the real ``~/.stackowl/``.

The TIMEOUT path is driven with a FAKE clock so it is deterministic (no real
5-minute wall wait): the tool computes ``deadline = clock.monotonic() + timeout``
and loops ``while clock.monotonic() < deadline``, so advancing the fake clock past
the deadline forces the timeout branch immediately. The injected clock governs
ONLY the deadline; ``asyncio.sleep`` between polls is real but tiny.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.process.registry import ProcessRegistry
from stackowl.tools.process.process_tool import ProcessTool
from stackowl.tools.process.wait_tool import WaitTool


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


def py(code: str) -> list[str]:
    """A cross-platform argv running ``code`` via the test interpreter."""
    return [sys.executable, "-u", "-c", code]


class _ManualClock:
    """A monotonic clock the test advances by hand (deterministic deadlines)."""

    def __init__(self) -> None:
        self._t = 1000.0

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


async def _payload(result) -> dict:
    assert result.success, result.error
    return json.loads(result.output)


class _Ctx:
    """A real registry + trace session for a test, torn down on exit. The wait
    tool can be given a custom clock for the deterministic timeout path."""

    def __init__(self, session_id: str = "sess-1", *, clock=None) -> None:
        self._session_id = session_id
        self.registry = ProcessRegistry()
        self.process = ProcessTool()
        self.wait = WaitTool(clock=clock)

    async def __aenter__(self) -> _Ctx:
        self._token = set_services(StepServices(process_registry=self.registry))
        self._trace = TraceContext.start(self._session_id, trace_id="tr-wait", channel="cli")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.registry.clear_all()
        TraceContext.reset(self._trace)
        reset_services(self._token)


# ----------------------------------------------------------------- duration
@pytest.mark.asyncio
async def test_duration_wait_sleeps_and_reports_satisfied() -> None:
    async with _Ctx() as ctx:
        res = await ctx.wait.execute(seconds=0.05)
        data = await _payload(res)
        assert data["mode"] == "duration"
        assert data["satisfied"] is True
        assert data["waited"] == 0.05


@pytest.mark.asyncio
async def test_duration_wait_is_clamped_to_max() -> None:
    from stackowl.process.limits import WAIT_MAX_TIMEOUT_SECONDS

    async with _Ctx() as ctx:
        # A wildly-too-long request is clamped (we assert the REPORTED span, not a
        # real sleep — asyncio.sleep is monkeypatched to a no-op so the test is fast).
        async def _noop(_s):  # noqa: ANN001, ANN202
            return None

        import stackowl.tools.process.wait_tool as wt

        orig = wt.asyncio.sleep
        wt.asyncio.sleep = _noop  # type: ignore[assignment]
        try:
            data = await _payload(await ctx.wait.execute(seconds=10_000.0))
        finally:
            wt.asyncio.sleep = orig  # type: ignore[assignment]
        assert data["waited"] == WAIT_MAX_TIMEOUT_SECONDS
        assert data["satisfied"] is True


# ------------------------------------------------------------- process-exit
@pytest.mark.asyncio
async def test_wait_for_process_until_it_exits() -> None:
    async with _Ctx() as ctx:
        start = await _payload(
            await ctx.process.execute(
                action="start", command=py("import time; time.sleep(0.15); print('done')")
            )
        )
        pid = start["process_id"]
        res = await ctx.wait.execute(for_process=pid, timeout=5.0)
        data = await _payload(res)
        assert data["mode"] == "process"
        assert data["process_id"] == pid
        assert data["satisfied"] is True  # it genuinely exited
        assert data["status"] == "exited"
        assert data["exit_code"] == 0
        assert data["waited"] >= 0.0


@pytest.mark.asyncio
async def test_wait_for_process_times_out_while_still_running() -> None:
    clock = _ManualClock()
    async with _Ctx(clock=clock) as ctx:
        # A long-lived child that will NOT exit within the wait window.
        start = await _payload(
            await ctx.process.execute(action="start", command=py("import time; time.sleep(30)"))
        )
        pid = start["process_id"]

        # Run the wait concurrently and shove the fake clock past the deadline so
        # the timeout branch fires deterministically (no real 30s wait).
        task = asyncio.create_task(ctx.wait.execute(for_process=pid, timeout=2.0))
        await asyncio.sleep(0.01)
        clock.advance(5.0)  # past deadline = start(1000) + 2.0
        res = await asyncio.wait_for(task, timeout=5.0)

        data = await _payload(res)
        assert data["mode"] == "process"
        assert data["satisfied"] is False  # timed out — still running
        assert data["status"] == "running"


@pytest.mark.asyncio
async def test_wait_does_not_busy_spin_polls_bounded() -> None:
    """The poll loop sleeps WAIT_POLL_INTERVAL_SECONDS between polls — NOT a busy
    spin. We spy on registry.poll and assert it was called only a SMALL bounded
    number of times across a multi-poll-interval wait (a busy spin would call it
    thousands of times)."""
    async with _Ctx() as ctx:
        # A child that exits after ~1.2s — long enough to span a few poll intervals.
        start = await _payload(
            await ctx.process.execute(action="start", command=py("import time; time.sleep(1.2)"))
        )
        pid = start["process_id"]

        real_poll = ctx.registry.poll
        calls = {"n": 0}

        async def _counting_poll(process_id, session_id=None):  # noqa: ANN001, ANN202
            calls["n"] += 1
            return await real_poll(process_id, session_id)

        ctx.registry.poll = _counting_poll  # type: ignore[assignment]
        data = await _payload(await ctx.wait.execute(for_process=pid, timeout=10.0))
        assert data["satisfied"] is True
        # ~1.2s / 0.5s interval ≈ 3-4 polls. A busy spin would be in the thousands.
        # Generous upper bound proves it is interval-paced, not spinning.
        assert calls["n"] <= 12, f"expected a handful of polls, got {calls['n']} (busy spin?)"


# --------------------------------------------------------------- self-heal
@pytest.mark.asyncio
async def test_wait_unknown_process_is_structured_error() -> None:
    async with _Ctx() as ctx:
        res = await ctx.wait.execute(for_process="does-not-exist", timeout=1.0)
        assert not res.success
        assert "no such process" in (res.error or "")


@pytest.mark.asyncio
async def test_wait_other_session_process_is_hidden_no_such_process() -> None:
    async with _Ctx("sess-A") as ctx:
        # A process owned by a DIFFERENT session — Fork E scoping hides it.
        other = await ctx.registry.start(py("import time; time.sleep(30)"), session_id="sess-B")
        res = await ctx.wait.execute(for_process=other.process_id, timeout=1.0)
        assert not res.success
        assert "no such process" in (res.error or "")


@pytest.mark.asyncio
async def test_wait_none_registry_is_structured_unavailable() -> None:
    tool = WaitTool()
    token = set_services(StepServices(process_registry=None))
    trace = TraceContext.start("sess-x", trace_id="tr", channel="cli")
    try:
        res = await tool.execute(for_process="anything", timeout=1.0)
        assert not res.success
        assert "unavailable" in (res.error or "")
    finally:
        TraceContext.reset(trace)
        reset_services(token)


# --------------------------------------------------------------- arg surface
@pytest.mark.asyncio
async def test_wait_requires_exactly_one_mode() -> None:
    async with _Ctx() as ctx:
        # Neither mode.
        res = await ctx.wait.execute()
        assert not res.success
        assert "requires either" in (res.error or "")
        # Both modes.
        res = await ctx.wait.execute(seconds=1.0, for_process="x")
        assert not res.success
        assert "EXACTLY one" in (res.error or "")


@pytest.mark.asyncio
async def test_wait_rejects_unknown_arg() -> None:
    async with _Ctx() as ctx:
        res = await ctx.wait.execute(bogus=1)
        assert not res.success
        assert "invalid arguments" in (res.error or "")


# ------------------------------------------------------------ cancellation
@pytest.mark.asyncio
async def test_wait_propagates_cancellation_not_swallowed() -> None:
    """A new user message cancels the turn → the in-flight wait must propagate
    CancelledError (never eat it). We start a wait on a never-exiting process,
    cancel the task, and assert the cancellation surfaced."""
    async with _Ctx() as ctx:
        start = await _payload(
            await ctx.process.execute(action="start", command=py("import time; time.sleep(30)"))
        )
        pid = start["process_id"]
        task = asyncio.create_task(ctx.wait.execute(for_process=pid, timeout=60.0))
        await asyncio.sleep(0.05)  # let it enter the poll loop / sleep
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --------------------------------------------------------------- manifest
def test_wait_manifest_is_read_severity_and_process_group() -> None:
    m = WaitTool().manifest
    assert m.name == "wait"
    assert m.action_severity == "read"  # never spends, never gated
    assert m.toolset_group == "process"


def test_wait_registered_in_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    assert reg.get("wait") is not None
