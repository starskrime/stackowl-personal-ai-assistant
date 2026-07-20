"""`stackowl health` must report MCP server liveness when MCP is configured.

Found during code review: `McpHealthContributor` existed and was already wired
into the `serve`-process aggregator (scheduler/assembly.py), but the
out-of-process `stackowl health` CLI command never registered it — a
configured, dead MCP server was invisible to `stackowl health`. Unlike
ResilienceContributor (which genuinely needs live in-process state the CLI
can't have), McpLivenessProbe is a fresh, dependency-free probe per config —
there was no reason for the CLI to skip it.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from stackowl.cli.app import app
from stackowl.mcp.allowlist import McpServerConfig

runner = CliRunner()


class _FakeMcpClientSettings:
    def __init__(self, servers: list[McpServerConfig]) -> None:
        self.servers = servers


class _FakeSettings:
    def __init__(self, servers: list[McpServerConfig]) -> None:
        self.providers: list[object] = []
        self.mcp_client = _FakeMcpClientSettings(servers)


def test_health_reports_dead_mcp_server() -> None:
    servers = [
        McpServerConfig(name="ghost", uri="stdio:///nonexistent/ghost-server", timeout_seconds=1.0)
    ]
    with patch("stackowl.config.settings.Settings", return_value=_FakeSettings(servers)):
        result = runner.invoke(app, ["health"])

    assert "mcp" in result.output.lower(), result.output
    assert "ghost" in result.output.lower(), result.output


def test_health_omits_mcp_when_none_configured() -> None:
    with patch("stackowl.config.settings.Settings", return_value=_FakeSettings([])):
        result = runner.invoke(app, ["health"])

    assert "mcp " not in result.output.lower(), result.output
