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

_LEN = struct.Struct(">I")


# --- fakes -----------------------------------------------------------------------


class _FakeResult:
    def __init__(self, *, success: bool, output: str = "", error: str | None = None) -> None:
        self.success = success
        self.output = output
        self.error = error


class _SpyTool:
    """Records every execute() call so a test can prove a tool ran (or did NOT)."""

    def __init__(self, *, output: str = "OK", success: bool = True, delay: float = 0.0) -> None:
        self.calls: list[dict[str, object]] = []
        self._output = output
        self._success = success
        self._delay = delay

    async def execute(self, **kwargs: object) -> _FakeResult:
        self.calls.append(dict(kwargs))
        if self._delay:
            await asyncio.sleep(self._delay)
        return _FakeResult(success=self._success, output=self._output)


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
        session_id="sess-1", audit_logger=audit, limits=limits,
    )
    return server, registry


# --- allowlist / hard-exclusion --------------------------------------------------


class TestAllowlistDefaultDeny:
    async def test_allowed_tool_runs_via_registry(self, tmp_path: Path) -> None:
        spy = _SpyTool(output="hello")
        server, _ = _server(tmp_path, tools={"read_file": spy})
        async with server:
            resp = await _call(server.socket_path, "read_file", {"path": "x.txt"})
        assert resp["result"] == "hello"
        assert spy.calls == [{"path": "x.txt"}], "the allowed tool was actually invoked"

    @pytest.mark.parametrize("excluded", ["shell", "execute_code", "process", "delegate_task", "send_message"])
    async def test_hard_excluded_refused_without_invoking(self, tmp_path: Path, excluded: str) -> None:
        # A hard-excluded name must be refused WITHOUT the registry ever resolving it.
        spy = _SpyTool()
        server, registry = _server(tmp_path, tools={excluded: spy})
        async with server:
            resp = await _call(server.socket_path, excluded, {"x": 1})
        assert "error" in resp
        assert "not callable from a sandbox" in resp["error"]
        assert spy.calls == [], f"{excluded} was INVOKED — hard-exclusion breached"
        assert excluded not in registry.lookups, f"{excluded} was even looked up in the registry"

    async def test_unknown_tool_refused(self, tmp_path: Path) -> None:
        server, registry = _server(tmp_path, tools={})
        async with server:
            resp = await _call(server.socket_path, "totally_made_up", {})
        assert "not callable from a sandbox" in resp["error"]
        assert registry.lookups == []


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
