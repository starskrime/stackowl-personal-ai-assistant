"""Every path that runs a registered tool goes through ``Tool.__call__``.

FOUND 2026-08-19, wiring D16.1's pre/post_tool_call hooks into that chokepoint:
three call sites ran ``tool.execute(**args)`` directly and therefore skipped
everything ``__call__`` is for —

    mcp/server.py                       an external MCP client's call
    tools/interaction/_batch_support.py batch approve
    sandbox/ptc/dispatch.py             a sandboxed script calling a host tool

and what they skipped is not incidental. ``__call__`` is where the platform times
the call, wraps a raise into a failed ``ToolResult`` instead of letting it escape,
runs the bounded retry-once for read-severity transients, runs ``verify()`` and the
ACCEPTANCE AUTHORITY — the objective check this whole programme is built on — emits
the next-step signal, and dispatches the lifecycle hooks. On those three paths a
tool's success was whatever the tool said it was.

THE PTC BYPASS WAS DOCUMENTED AS DELIBERATE and the documentation had gone stale.
It gave two reasons: consent must not be re-prompted, and it must work under a
test-mode guard. There is NO consent gate in ``__call__`` — consent lives in the
pipeline dispatch — so the first reason describes something that does not happen.
The second is real and is a HOLE rather than a feature: ``TestModeGuard`` exists to
block live I/O when the platform runs in test mode, and a sandboxed script calling a
real host tool IS live I/O. PTC is now default-ALLOW minus sandbox-escape vectors,
so ``send_message``, ``owl_build`` and ``skill_manage`` are reachable from a script —
which is where an unverified success matters most, not least.

Each test drives the REAL dispatcher with a REAL ``Tool`` subclass, because a double
that only implements ``execute()`` is exactly what let this survive: the registries
these dispatchers read hold ``Tool`` instances, which are callable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stackowl.tools.base import Tool, ToolResult


class _LyingTool(Tool):
    """Claims success; its own verify() says the effect never happened.

    ``verified is False`` on the result is proof that ``__call__`` ran — nothing
    else in the platform consults ``verify()``.
    """

    @property
    def name(self) -> str:
        return "liar"

    @property
    def description(self) -> str:
        return "A tool that claims a success reality refuses to confirm."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="I did the thing")

    async def verify(
        self, call_args: dict[str, object], result: ToolResult, *, started_at: float = 0.0
    ) -> bool | None:
        return False


class _RaisingTool(_LyingTool):
    @property
    def name(self) -> str:
        return "exploder"

    async def execute(self, **kwargs: object) -> ToolResult:
        raise RuntimeError("the tool blew up")


class _HonestTool(_LyingTool):
    """No verify() opinion — the ~92 un-migrated tools. Must behave as before."""

    @property
    def name(self) -> str:
        return "honest"

    async def verify(
        self, call_args: dict[str, object], result: ToolResult, *, started_at: float = 0.0
    ) -> bool | None:
        return None


class _Registry:
    def __init__(self, *tools: Tool) -> None:
        self._by_name = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def all(self) -> list[Tool]:
        return list(self._by_name.values())


# ------------------------------------------------------------------ MCP server


class _Wire:
    """The two decorators mcp.Server exposes, capturing the handler."""

    def __init__(self) -> None:
        self.call: Any = None

    def list_tools(self) -> Any:
        return lambda fn: fn

    def call_tool(self) -> Any:
        def _register(fn: Any) -> Any:
            self.call = fn
            return fn

        return _register


def _mcp_handler(*tools: Tool) -> Any:
    from stackowl.mcp.server import _wire_handlers
    from stackowl.mcp.tool_exposure import McpToolExposurePolicy

    wire = _Wire()
    _wire_handlers(wire, _Registry(*tools), McpToolExposurePolicy(allow_consequential=True))
    return wire.call


class TestTheMcpServer:
    async def test_a_refuted_claim_is_not_returned_as_a_success(self) -> None:
        """An external client's call used to be verified by NOTHING: the tool's own
        claim of success went straight back out as the answer."""
        out = await _mcp_handler(_LyingTool())("liar", {})

        assert getattr(out, "isError", False) is True
        assert "could not confirm" in str(out.content[0].text)

    async def test_a_raising_tool_does_not_escape_into_the_server(self) -> None:
        """``__call__`` wraps a raise into a failed ToolResult. Without it the
        exception left the handler and the client saw a transport-level failure
        instead of a tool failure it could act on."""
        out = await _mcp_handler(_RaisingTool())("exploder", {})

        assert getattr(out, "isError", False) is True
        assert "blew up" in str(out.content[0].text)

    async def test_an_ordinary_tool_is_unchanged(self) -> None:
        """verified=None falls back to the self-report, so the ~92 tools that do not
        verify behave exactly as they did."""
        out = await _mcp_handler(_HonestTool())("honest", {})

        assert out[0].text == "I did the thing"


# ----------------------------------------------------------------- PTC dispatch


def _invoker(registry: _Registry, workspace: Path) -> Any:
    from stackowl.sandbox.ptc.dispatch import PtcToolInvoker
    from stackowl.sandbox.ptc.protocol import PtcLimits

    return PtcToolInvoker(
        registry=registry, workspace=workspace, session_key="test",
        trace_id="t", audit_logger=None, limits=PtcLimits(),
    )


class TestTheSandboxDispatcher:
    async def test_a_host_tool_called_from_a_script_is_verified(
        self, tmp_path: Path
    ) -> None:
        out = await _invoker(_Registry(_LyingTool()), tmp_path).invoke("liar", {})

        assert out["verified"] is False, "the verdict must ride back to the script"
        assert out["success"] is False, "a refuted claim is not a success here either"
        assert "could not confirm" in str(out["error"])

    async def test_a_raising_host_tool_becomes_a_result(self, tmp_path: Path) -> None:
        out = await _invoker(_Registry(_RaisingTool()), tmp_path).invoke("exploder", {})

        assert out["success"] is False
        assert "blew up" in str(out["error"])

    async def test_an_ordinary_tool_is_unchanged(self, tmp_path: Path) -> None:
        out = await _invoker(_Registry(_HonestTool()), tmp_path).invoke("honest", {})

        assert out["success"] is True
        assert out["output"] == "I did the thing"
        assert out["verified"] is None


# ---------------------------------------------------------------- batch approve


def _executor(registry: _Registry) -> Any:
    from stackowl.tools.interaction._batch_support import BatchAuditor, BatchExecutor

    class _Services:
        audit_logger = None

    return BatchExecutor(
        registry,  # type: ignore[arg-type]
        BatchAuditor(_Services(), actor="test", clock=lambda: 0.0),  # type: ignore[arg-type]
    )


def _action(tool: str) -> Any:
    from stackowl.tools.interaction._batch_support import BatchAction

    return BatchAction(tool=tool, args={}, summary=f"run {tool}")


class TestBatchApprove:
    async def test_an_approved_action_that_did_not_happen_is_not_reported_as_done(
        self,
    ) -> None:
        """The batch the user explicitly approved was the one flow reporting an
        action's own claim back to them with nothing checking it."""
        outcome = await _executor(_Registry(_LyingTool()))._run_one(  # noqa: SLF001
            1, _action("liar"), "session"
        )

        assert outcome["success"] is False
        assert outcome["verified"] is False
        assert "could not confirm" in str(outcome["error"])

    async def test_a_raising_action_still_never_raises(self) -> None:
        """B5 — one bad action must not take the batch down. That behaviour predates
        this change and must survive it."""
        outcome = await _executor(_Registry(_RaisingTool()))._run_one(  # noqa: SLF001
            1, _action("exploder"), "session"
        )

        assert outcome["success"] is False
        assert "blew up" in str(outcome["error"])

    async def test_an_ordinary_action_is_unchanged(self) -> None:
        outcome = await _executor(_Registry(_HonestTool()))._run_one(  # noqa: SLF001
            1, _action("honest"), "session"
        )

        assert outcome["success"] is True
        assert outcome["output"] == "I did the thing"
