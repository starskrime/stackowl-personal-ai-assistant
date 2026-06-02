"""Tool-level tests for the ``process`` tool (E9-S1).

These drive the TOOL surface (not the registry directly), with a REAL
:class:`ProcessRegistry` injected via ``set_services`` and a ``TraceContext``
session id (mirroring the delegate_task/MoA tool tests). Subprocesses use
``sys.executable -c "..."`` so the suite runs identically on Windows and POSIX
([[feedback_cross_platform]]). ``STACKOWL_HOME`` is pointed at a tmp dir so the
registry checkpoint never touches the real ``~/.stackowl/``.

A real :class:`WallClock` is fine here: every child exits in well under a second
and no test exercises a TTL/deadline (the S0 suite covers those deterministically).
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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


def py(code: str) -> list[str]:
    """A cross-platform argv running ``code`` via the test interpreter."""
    return [sys.executable, "-u", "-c", code]


async def _payload(result) -> dict:
    """Decode a successful tool result's JSON output."""
    assert result.success, result.error
    return json.loads(result.output)


async def _wait_terminal(tool: ProcessTool, pid: str, timeout: float = 5.0) -> dict:
    """Poll the TOOL until the process reaches a terminal state (bounded)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        res = await tool.execute(action="poll", process_id=pid)
        data = await _payload(res)
        if not data["running"]:
            return data
        await asyncio.sleep(0.05)
    raise AssertionError("process did not reach terminal state in time")


class _Ctx:
    """Set up a real registry + trace session for a test, tearing both down."""

    def __init__(self, session_id: str = "sess-1") -> None:
        self._session_id = session_id
        self.registry = ProcessRegistry()
        self.tool = ProcessTool()

    async def __aenter__(self) -> _Ctx:
        self._token = set_services(StepServices(process_registry=self.registry))
        self._trace = TraceContext.start(self._session_id, trace_id="tr-proc", channel="cli")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.registry.clear_all()
        TraceContext.reset(self._trace)
        reset_services(self._token)


# --------------------------------------------------------------------- start
@pytest.mark.asyncio
async def test_start_returns_process_id_and_is_session_scoped() -> None:
    async with _Ctx() as ctx:
        res = await ctx.tool.execute(action="start", command=py("print('hi')"))
        data = await _payload(res)
        assert data["action"] == "start"
        assert data["process_id"]
        assert data["pid"]
        assert data["status"] == "running"
        # The handle landed in the registry under the caller's session.
        scoped = ctx.registry.list("sess-1")
        assert any(h.process_id == data["process_id"] for h in scoped)


@pytest.mark.asyncio
async def test_start_requires_command() -> None:
    async with _Ctx() as ctx:
        res = await ctx.tool.execute(action="start")
        assert not res.success
        assert "command" in (res.error or "")


# ---------------------------------------------------------------------- poll
@pytest.mark.asyncio
async def test_poll_reaches_terminal_with_exit_code() -> None:
    async with _Ctx() as ctx:
        start = await _payload(await ctx.tool.execute(action="start", command=py("import sys; sys.exit(3)")))
        data = await _wait_terminal(ctx.tool, start["process_id"])
        assert data["status"] == "failed"
        assert data["exit_code"] == 3
        assert data["running"] is False


# ----------------------------------------------------------------------- log
@pytest.mark.asyncio
async def test_log_returns_captured_stdout() -> None:
    async with _Ctx() as ctx:
        start = await _payload(
            await ctx.tool.execute(action="start", command=py("print('hello-from-child')"))
        )
        await _wait_terminal(ctx.tool, start["process_id"])
        res = await ctx.tool.execute(action="log", process_id=start["process_id"])
        data = await _payload(res)
        assert "hello-from-child" in data["stdout"]
        assert "stderr" in data  # both streams by default


# --------------------------------------------------------------- write/submit
@pytest.mark.asyncio
async def test_submit_and_write_feed_stdin() -> None:
    # A child that echoes each stdin line back to stdout, then exits on EOF.
    code = "import sys\nfor line in sys.stdin:\n    sys.stdout.write('echo:' + line)\n    sys.stdout.flush()"
    async with _Ctx() as ctx:
        start = await _payload(await ctx.tool.execute(action="start", command=py(code)))
        pid = start["process_id"]
        sub = await _payload(await ctx.tool.execute(action="submit", process_id=pid, line="alpha"))
        assert sub["written"] == len("alpha\n")
        wr = await _payload(await ctx.tool.execute(action="write", process_id=pid, data="beta\n"))
        assert wr["written"] == len("beta\n")
        # Close stdin → the child sees EOF and exits.
        await ctx.tool.execute(action="close", process_id=pid)
        await _wait_terminal(ctx.tool, pid)
        log = await _payload(await ctx.tool.execute(action="log", process_id=pid, stream="stdout"))
        assert "echo:alpha" in log["stdout"]
        assert "echo:beta" in log["stdout"]


# ----------------------------------------------------------------------- kill
@pytest.mark.asyncio
async def test_kill_terminates_a_running_process() -> None:
    async with _Ctx() as ctx:
        start = await _payload(
            await ctx.tool.execute(action="start", command=py("import time; time.sleep(30)"))
        )
        pid = start["process_id"]
        killed = await _payload(await ctx.tool.execute(action="kill", process_id=pid))
        assert killed["killed"] is True
        poll = await _payload(await ctx.tool.execute(action="poll", process_id=pid))
        assert poll["running"] is False
        assert poll["status"] == "killed"


# ----------------------------------------------------------------------- list
@pytest.mark.asyncio
async def test_list_session_scoped_vs_all_cross_session() -> None:
    async with _Ctx("sess-A") as ctx:
        mine = await _payload(await ctx.tool.execute(action="start", command=py("import time; time.sleep(30)")))
        # A second process owned by a DIFFERENT session, inserted into the same registry.
        other = await ctx.registry.start(py("import time; time.sleep(30)"), session_id="sess-B")
        # Default list → only the caller's session.
        scoped = await _payload(await ctx.tool.execute(action="list"))
        ids = {row["process_id"] for row in scoped["processes"]}
        assert mine["process_id"] in ids
        assert other.process_id not in ids
        assert scoped["all"] is False
        # all=True → audited cross-session view sees both.
        every = await _payload(await ctx.tool.execute(action="list", all=True))
        all_ids = {row["process_id"] for row in every["processes"]}
        assert mine["process_id"] in all_ids
        assert other.process_id in all_ids


# ------------------------------------------------------------------ refusals
@pytest.mark.asyncio
async def test_count_cap_surfaces_structured_not_raised() -> None:
    # A registry capped at 1 live process; the second start must REFUSE structured.
    registry = ProcessRegistry(max_processes=1)
    tool = ProcessTool()
    token = set_services(StepServices(process_registry=registry))
    trace = TraceContext.start("sess-cap", trace_id="tr", channel="cli")
    try:
        first = await _payload(await tool.execute(action="start", command=py("import time; time.sleep(30)")))
        assert first["process_id"]
        res = await tool.execute(action="start", command=py("import time; time.sleep(30)"))
        data = await _payload(res)  # structured success result carrying the refusal
        assert data["refused"] is True
        assert data["reason"] == "too_many_processes"
    finally:
        await registry.clear_all()
        TraceContext.reset(trace)
        reset_services(token)


@pytest.mark.asyncio
async def test_unknown_process_id_is_structured_error() -> None:
    async with _Ctx() as ctx:
        for action in ("poll", "log", "kill"):
            res = await ctx.tool.execute(action=action, process_id="does-not-exist")
            assert not res.success
            assert "no such process" in (res.error or "")


@pytest.mark.asyncio
async def test_missing_process_id_is_structured_error() -> None:
    async with _Ctx() as ctx:
        res = await ctx.tool.execute(action="poll")
        assert not res.success
        assert "process_id" in (res.error or "")


@pytest.mark.asyncio
async def test_unknown_action_is_structured_error() -> None:
    async with _Ctx() as ctx:
        res = await ctx.tool.execute(action="frobnicate")
        assert not res.success
        assert "unknown action" in (res.error or "")


@pytest.mark.asyncio
async def test_none_registry_is_structured_unavailable() -> None:
    tool = ProcessTool()
    token = set_services(StepServices(process_registry=None))
    trace = TraceContext.start("sess-x", trace_id="tr", channel="cli")
    try:
        res = await tool.execute(action="start", command=py("print('x')"))
        assert not res.success
        assert "unavailable" in (res.error or "")
    finally:
        TraceContext.reset(trace)
        reset_services(token)


@pytest.mark.asyncio
async def test_write_to_unknown_process_is_structured_error() -> None:
    async with _Ctx() as ctx:
        res = await ctx.tool.execute(action="write", process_id="ghost", data="x")
        assert not res.success
        assert "could not write" in (res.error or "")
