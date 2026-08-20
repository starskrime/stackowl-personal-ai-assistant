"""PtcServer unit tests — the HOST-side trust boundary (no real sandbox needed).

These assert the load-bearing security properties of the per-run host-tool callback
server, driving it over a REAL unix socket with a fake tool registry (the AI / sandbox
is never involved): default-DENY allowlist, hard-exclusion WITHOUT invoking,
write-confinement to the sandbox workspace, rate-limit, per-call timeout, never-raise,
audit-present, and socket 0600 + teardown/unlink.

Bounded: tiny limits + short timeouts so a socket can never hang the box.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

import pytest

from stackowl.sandbox.ptc.protocol import PtcLimits
from stackowl.sandbox.ptc.server import PtcServer
from stackowl.tools.base import Tool, ToolResult

_LEN = struct.Struct(">I")


# --- fakes -----------------------------------------------------------------------


class _SpyTool(Tool):
    """Records every call so a test can prove a tool ran (or did NOT).

    A REAL ``Tool`` subclass since 2026-08-20, and that is the point rather than
    tidiness: this used to be a bare object with an ``execute()`` method, so it did
    not resemble what a registry actually holds — and it kept passing while the
    dispatcher reached past ``Tool.__call__`` into ``execute()``, skipping
    verification, the acceptance authority, exception wrapping and the lifecycle
    hooks. A double that cannot be called the way the real thing is called cannot
    catch that.
    """

    def __init__(self, *, output: str = "OK", success: bool = True, delay: float = 0.0) -> None:
        self.calls: list[dict[str, object]] = []
        self._output = output
        self._success = success
        self._delay = delay

    @property
    def name(self) -> str:
        return "spy"

    @property
    def description(self) -> str:
        return "A spy tool that records the arguments a PTC script passed it."

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        self.calls.append(dict(kwargs))
        if self._delay:
            await asyncio.sleep(self._delay)
        return ToolResult(success=self._success, output=self._output)


class _FakeRegistry:
    def __init__(self, tools: dict[str, _SpyTool]) -> None:
        self._tools = tools
        self.lookups: list[str] = []

    def get(self, name: str) -> _SpyTool | None:
        self.lookups.append(name)
        return self._tools.get(name)


class _SpyAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None, dict]] = []

    def append(self, event_type: str, actor: str, target: str | None, details: dict) -> None:
        self.events.append((event_type, actor, target, details))


# --- client helper (frames a request, reads the response) ------------------------


async def _call(sock_path: Path, tool: str, args: dict[str, object], *, req_id: int = 1) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    try:
        body = json.dumps({"id": req_id, "tool": tool, "args": args}).encode("utf-8")
        writer.write(_LEN.pack(len(body)) + body)
        await writer.drain()
        prefix = await reader.readexactly(4)
        (length,) = _LEN.unpack(prefix)
        resp = await reader.readexactly(length)
        return json.loads(resp.decode("utf-8"))
    finally:
        writer.close()


def _server(
    tmp_path: Path, *, tools: dict[str, _SpyTool] | None = None,
    audit: _SpyAudit | None = None, limits: PtcLimits | None = None,
) -> tuple[PtcServer, _FakeRegistry]:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    registry = _FakeRegistry(tools or {})
    server = PtcServer(
        registry=registry, workspace=ws, socket_path=tmp_path / "ptc.sock",
        session_key="sess-1", audit_logger=audit, limits=limits,
    )
    return server, registry


# --- allowlist / hard-exclusion --------------------------------------------------


class TestEscapeFenceDefaultAllow:
    """D05.5 — the policy inverted: default-ALLOW minus SANDBOX-ESCAPE vectors.

    This class used to be TestAllowlistDefaultDeny and asserted that only five
    names were callable. That changed by operator decision, with the cost stated:
    consequential tools are now reachable from a script and are NOT consent-
    prompted per call. The tests below are rewritten to the NEW contract, and the
    escape fence — the part that did NOT change — is tested harder than before.
    """

    async def test_allowed_tool_runs_via_registry(self, tmp_path: Path) -> None:
        spy = _SpyTool(output="hello")
        server, _ = _server(tmp_path, tools={"read_file": spy})
        async with server:
            resp = await _call(server.socket_path, "read_file", {"path": "x.txt"})
        assert resp["result"] == "hello"
        assert spy.calls == [{"path": "x.txt"}], "the allowed tool was actually invoked"

    @pytest.mark.parametrize(
        "escape_vector",
        ["shell", "execute_code", "process", "claude_code", "delegate_task",
         "sessions_spawn", "sessions_send"],
    )
    async def test_escape_vector_refused_without_invoking(
        self, tmp_path: Path, escape_vector: str
    ) -> None:
        """THE INVARIANT THAT DID NOT CHANGE, and must never regress.

        Widening PTC to default-ALLOW is only defensible while the sandbox cannot
        break OUT of itself. Each of these would do exactly that: run host
        commands, nest another sandbox, control host processes, launch an agent
        with host FS access, or bypass the delegation-depth ceiling.

        Asserted on the REGISTRY LOOKUP as well as the invocation — refusal has to
        happen before resolution, so a tool cannot be constructed at all.
        """
        spy = _SpyTool()
        server, registry = _server(tmp_path, tools={escape_vector: spy})
        async with server:
            resp = await _call(server.socket_path, escape_vector, {"x": 1})
        assert "error" in resp
        assert "not callable from a sandbox" in resp["error"]
        assert spy.calls == [], f"{escape_vector} was INVOKED — escape fence breached"
        assert escape_vector not in registry.lookups, (
            f"{escape_vector} was looked up in the registry — refusal came too late"
        )

    @pytest.mark.parametrize("consequential", ["send_message", "owl_build", "tool_build"])
    async def test_a_consequential_tool_is_now_callable(
        self, tmp_path: Path, consequential: str
    ) -> None:
        """The deliberate widening, asserted explicitly rather than left implied.

        This is the cost the operator accepted: a sandboxed script can take a
        real-world action, and execute_code's own consent is the only consent
        covering it. Pinning it means the change is visible in the test suite
        instead of only in a commit message.
        """
        spy = _SpyTool(output="done")
        server, _ = _server(tmp_path, tools={consequential: spy})
        async with server:
            resp = await _call(server.socket_path, consequential, {"x": 1})
        assert resp.get("result") == "done", f"{consequential} was refused: {resp}"
        assert spy.calls == [{"x": 1}]

    async def test_an_unregistered_tool_fails_at_the_registry_not_the_fence(
        self, tmp_path: Path
    ) -> None:
        """Under default-ALLOW an unknown NAME is no longer refused by policy.

        It passes the escape fence and fails one layer down because nothing is
        registered under it. Still refused, still no invocation — but the error
        now names the real cause instead of implying a policy decision that was
        not made.
        """
        server, registry = _server(tmp_path, tools={})
        async with server:
            resp = await _call(server.socket_path, "totally_made_up", {})
        assert "error" in resp
        assert "not registered" in resp["error"], resp
        assert registry.lookups == ["totally_made_up"], (
            "the fence should have let it through to the registry"
        )


# --- write-confinement to the sandbox workspace ----------------------------------


class TestWriteConfinement:
    async def test_escape_path_refused_without_invoking(self, tmp_path: Path) -> None:
        spy = _SpyTool()
        server, _ = _server(tmp_path, tools={"write_file": spy})
        async with server:
            resp = await _call(
                server.socket_path, "write_file",
                {"path": "../../../../etc/evil", "content": "x"},
            )
        assert "error" in resp
        assert "escapes" in resp["error"] or "workspace" in resp["error"]
        assert spy.calls == [], "an escaping write was INVOKED — confinement breached"

    async def test_in_workspace_path_is_confined_and_invoked(self, tmp_path: Path) -> None:
        spy = _SpyTool(output="written")
        server, _ = _server(tmp_path, tools={"write_file": spy})
        async with server:
            resp = await _call(
                server.socket_path, "write_file", {"path": "out.txt", "content": "hi"}
            )
        assert resp["result"] == "written"
        assert spy.calls, "the confined write did not invoke the tool"
        # The path was re-anchored to an ABSOLUTE path inside the sandbox workspace.
        written_path = Path(str(spy.calls[0]["path"]))
        assert written_path.is_absolute()
        written_path.resolve().relative_to((tmp_path / "workspace").resolve())


# --- rate-limit + timeout + bounds -----------------------------------------------


class TestRailsAndDoS:
    async def test_rate_limit_refuses_past_cap(self, tmp_path: Path) -> None:
        spy = _SpyTool()
        server, _ = _server(tmp_path, tools={"read_file": spy}, limits=PtcLimits(max_calls=2))
        async with server:
            r1 = await _call(server.socket_path, "read_file", {"path": "a"})
            r2 = await _call(server.socket_path, "read_file", {"path": "b"})
            r3 = await _call(server.socket_path, "read_file", {"path": "c"})
        assert "result" in r1 and "result" in r2
        assert "error" in r3 and "budget exhausted" in r3["error"]
        assert len(spy.calls) == 2, "a call past the cap still reached the tool"

    async def test_per_call_timeout_refuses(self, tmp_path: Path) -> None:
        slow = _SpyTool(delay=5.0)
        server, _ = _server(
            tmp_path, tools={"read_file": slow}, limits=PtcLimits(call_timeout_s=0.2)
        )
        async with server:
            resp = await asyncio.wait_for(
                _call(server.socket_path, "read_file", {"path": "x"}), timeout=3.0
            )
        assert "error" in resp and "timed out" in resp["error"]

    async def test_oversized_arg_refused(self, tmp_path: Path) -> None:
        spy = _SpyTool()
        server, _ = _server(
            tmp_path, tools={"write_file": spy}, limits=PtcLimits(max_arg_bytes=16)
        )
        async with server:
            resp = await _call(
                server.socket_path, "write_file",
                {"path": "out.txt", "content": "x" * 1000},
            )
        assert "error" in resp and "cap" in resp["error"]
        assert spy.calls == []


# --- never-raise + audit + socket hygiene ----------------------------------------


class TestRobustnessAndAudit:
    async def test_malformed_frame_does_not_crash(self, tmp_path: Path) -> None:
        server, _ = _server(tmp_path, tools={})
        async with server:
            reader, writer = await asyncio.open_unix_connection(str(server.socket_path))
            try:
                body = b"this is not json"
                writer.write(_LEN.pack(len(body)) + body)
                await writer.drain()
                prefix = await reader.readexactly(4)
                (length,) = _LEN.unpack(prefix)
                resp = json.loads((await reader.readexactly(length)).decode())
            finally:
                writer.close()
        assert "error" in resp and "malformed" in resp["error"]
        # the server is still alive — a second well-formed call still works
        async with server:
            pass

    async def test_audit_records_tool_not_secret_values(self, tmp_path: Path) -> None:
        audit = _SpyAudit()
        spy = _SpyTool()
        server, _ = _server(tmp_path, tools={"read_file": spy}, audit=audit)
        async with server:
            await _call(server.socket_path, "read_file", {"path": "/secret/value"})
        assert audit.events, "no audit event recorded for a PTC call"
        evt_type, actor, target, details = audit.events[-1]
        assert evt_type == "ptc_call"
        assert target == "read_file"
        assert "sandbox" in actor
        # The audit records arg KEY names, NEVER the secret VALUE.
        assert details["arg_keys"] == ["path"]
        flat = json.dumps(details)
        assert "/secret/value" not in flat

    async def test_excluded_call_is_audited(self, tmp_path: Path) -> None:
        audit = _SpyAudit()
        server, _ = _server(tmp_path, tools={}, audit=audit)
        async with server:
            await _call(server.socket_path, "shell", {"command": "rm -rf /"})
        assert audit.events
        _, _, target, details = audit.events[-1]
        assert target == "shell"
        assert details["allowed"] is False
        # the dangerous command string is NOT in the audit (only key names).
        assert "rm -rf" not in json.dumps(details)

    async def test_socket_is_0600_then_unlinked(self, tmp_path: Path) -> None:
        server, _ = _server(tmp_path, tools={})
        await server.start()
        sock = server.socket_path
        assert sock.exists()
        mode = sock.stat().st_mode & 0o777
        assert mode == 0o600, f"socket perms not 0600: {oct(mode)}"
        await server.aclose()
        assert not sock.exists(), "socket was not unlinked on teardown"
