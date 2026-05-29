"""E1-S3 — MCP boot wiring: namespacing, sanitization, fail-soft fan-out.

Federated MCP tools register under mcp.<server>.<tool> (non-clobbering vs native
tools), descriptions are sanitized (no hidden zero-width/bidi injection vectors),
and a down/slow server never blocks boot (fail-soft).
"""

from __future__ import annotations

import asyncio

from stackowl.mcp._tool import McpTool, sanitize_mcp_schema, sanitize_mcp_text
from stackowl.mcp.allowlist import McpServerConfig
from stackowl.mcp.cache import McpToolDefinition
from stackowl.startup.mcp_register import run as mcp_register_run
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


def _defn(name: str, *, server: str = "fixture", desc: str = "a tool") -> McpToolDefinition:
    return McpToolDefinition(name=name, description=desc, server_name=server, input_schema={"type": "object"})


class _FakeClient:
    """Stands in for McpClient — records the raw tool name passed to call_tool."""

    def __init__(self) -> None:
        self.called_with: list[str] = []

    async def call_tool(self, config, tool_name, args):  # noqa: ANN001
        self.called_with.append(tool_name)
        return "remote-result"


# --------------------------------------------------------------------------- #
# sanitization
# --------------------------------------------------------------------------- #
def test_sanitize_strips_zero_width_and_control_chars() -> None:
    # zero-width space (200B), bidi override (202E), control (\x07) must be removed
    dirty = "do​ a‮ thing\x07 now"
    clean = sanitize_mcp_text(dirty)
    assert "​" not in clean and "‮" not in clean and "\x07" not in clean
    assert clean == "do a thing now"


def test_sanitize_caps_length() -> None:
    assert len(sanitize_mcp_text("x" * 10_000)) <= 500


def test_sanitize_preserves_normal_multilingual_text() -> None:
    assert sanitize_mcp_text("ファイルを読む") == "ファイルを読む"


# --------------------------------------------------------------------------- #
# McpTool namespacing + raw-name invocation
# --------------------------------------------------------------------------- #
async def test_mcp_tool_name_is_namespaced() -> None:
    tool = McpTool(_defn("read", server="files"), _FakeClient(), McpServerConfig(name="files", uri="stdio:///x"))  # type: ignore[arg-type]
    assert tool.name == "mcp.files.read"


async def test_mcp_tool_calls_server_with_raw_name() -> None:
    client = _FakeClient()
    tool = McpTool(_defn("read", server="files"), client, McpServerConfig(name="files", uri="stdio:///x"))  # type: ignore[arg-type]
    await tool.execute(path="/tmp/x")
    assert client.called_with == ["read"]  # raw name, NOT the namespaced one


async def test_mcp_tool_description_is_sanitized() -> None:
    tool = McpTool(_defn("read", desc="hi​there"), _FakeClient(), McpServerConfig(name="fixture", uri="stdio:///x"))  # type: ignore[arg-type]
    assert "​" not in tool.description


def test_mcp_tool_defaults_to_read_severity() -> None:
    # operator vote: un-annotated MCP tools default to read (full auto-trust)
    tool = McpTool(_defn("read"), _FakeClient(), McpServerConfig(name="fixture", uri="stdio:///x"))  # type: ignore[arg-type]
    assert tool.manifest.action_severity == "read"


# --------------------------------------------------------------------------- #
# non-clobbering vs native tools
# --------------------------------------------------------------------------- #
class _NativeShell(Tool):
    @property
    def name(self) -> str:
        return "read"  # same RAW name as the MCP tool above

    @property
    def description(self) -> str:
        return "native"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="native", duration_ms=1.0)


async def test_federated_tool_does_not_clobber_native_same_raw_name() -> None:
    reg = ToolRegistry()
    reg.register(_NativeShell())  # native "read"
    reg.register(McpTool(_defn("read", server="files"), _FakeClient(), McpServerConfig(name="files", uri="stdio:///x")))  # type: ignore[arg-type]
    assert reg.get("read").description == "native"  # native untouched
    assert reg.get("mcp.files.read") is not None  # federated under namespaced key


# --------------------------------------------------------------------------- #
# fan-out fail-soft
# --------------------------------------------------------------------------- #
class _RegisterClient:
    """register_server_tools that succeeds, raises, or hangs per server name."""

    def __init__(self, behavior: dict[str, str]) -> None:
        self._behavior = behavior

    async def register_server_tools(self, config, registry):  # noqa: ANN001
        mode = self._behavior.get(config.name, "ok")
        if mode == "raise":
            raise RuntimeError("server exploded")
        if mode == "hang":
            await asyncio.sleep(10)
        registry.register(McpTool(_defn("t", server=config.name), self, config))  # type: ignore[arg-type]
        return 1


async def test_run_is_fail_soft_on_error_and_timeout() -> None:
    reg = ToolRegistry()
    configs = [
        McpServerConfig(name="good", uri="stdio:///g"),
        McpServerConfig(name="bad", uri="stdio:///b"),
        McpServerConfig(name="slow", uri="stdio:///s", timeout_seconds=0.05),
    ]
    client = _RegisterClient({"good": "ok", "bad": "raise", "slow": "hang"})
    summary = await mcp_register_run(client, configs, reg)  # type: ignore[arg-type]
    assert summary["good"] == 1  # good server registered
    assert summary["bad"] == 0  # error → 0, boot continues
    assert summary["slow"] == 0  # timeout → 0, boot continues
    assert reg.get("mcp.good.t") is not None


# --------------------------------------------------------------------------- #
# schema sanitization (party MAJOR #1) — params are a trust boundary too
# --------------------------------------------------------------------------- #
def test_sanitize_schema_strips_injection_in_property_descriptions() -> None:
    poisoned = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "a path​‮hidden"}},
    }
    clean = sanitize_mcp_schema(poisoned)
    desc = clean["properties"]["path"]["description"]  # type: ignore[index]
    assert "​" not in desc and "‮" not in desc


def test_sanitize_schema_caps_depth() -> None:
    node: dict = {"type": "object"}
    cur = node
    for _ in range(50):
        cur["child"] = {"type": "object"}
        cur = cur["child"]
    clean = sanitize_mcp_schema(node)
    # walk depth of result is bounded (deep branch dropped)
    depth = 0
    c = clean
    while isinstance(c, dict) and "child" in c:
        depth += 1
        c = c["child"]  # type: ignore[assignment]
    assert depth <= 8


def test_sanitize_schema_non_dict_is_safe_object() -> None:
    assert sanitize_mcp_schema("not a schema") == {"type": "object"}
    assert sanitize_mcp_schema(None) == {"type": "object"}


def test_sanitize_schema_preserves_normal_schema() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    assert sanitize_mcp_schema(schema) == schema


async def test_mcp_tool_parameters_are_sanitized() -> None:
    defn = McpToolDefinition(
        name="t", description="d", server_name="s",
        input_schema={"type": "object", "properties": {"a": {"description": "x​y"}}},
    )
    tool = McpTool(defn, _FakeClient(), McpServerConfig(name="s", uri="stdio:///x"))  # type: ignore[arg-type]
    assert "​" not in tool.parameters["properties"]["a"]["description"]  # type: ignore[index]


# --------------------------------------------------------------------------- #
# allowlist default (party MAJOR #2) — localhost SSE allowed, remote denied
# --------------------------------------------------------------------------- #
def test_default_allowlist_permits_localhost_sse_and_stdio() -> None:
    from stackowl.mcp.allowlist import McpServerAllowlist
    from stackowl.mcp.settings import McpClientSettings

    allow = McpServerAllowlist(list(McpClientSettings().allowed_uri_prefixes))
    assert allow.is_allowed("sse://http://localhost:8080/sse") is True
    assert allow.is_allowed("stdio:///usr/local/bin/server") is True
    # a non-local SSE server must NOT be allowed by default
    assert allow.is_allowed("sse://http://evil.example:8080/sse") is False


async def test_run_skips_disabled_servers() -> None:
    reg = ToolRegistry()
    configs = [McpServerConfig(name="off", uri="stdio:///o", enabled=False)]
    summary = await mcp_register_run(_RegisterClient({}), configs, reg)  # type: ignore[arg-type]
    assert "off" not in summary
