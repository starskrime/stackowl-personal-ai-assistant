"""OPS-3 (F148) — `mcp status` / `list-clients` report real state, never lie.

`mcp_status` previously printed "not running" unconditionally even while the
in-process SSE server was up; `mcp_list_clients` printed "No active MCP clients"
without inspecting anything. Now `status` does a real TCP liveness probe against
the configured host:port, and never asserts "not running" while the port accepts
connections; `list-clients` reports truthfully that per-client tracking is
unavailable rather than fabricating "No active MCP clients".
"""

from __future__ import annotations

import socket
import threading
from unittest.mock import patch

from typer.testing import CliRunner

from stackowl.cli.app import app
from stackowl.startup.mcp_status_probe import McpStatusProbe

runner = CliRunner()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_probe_reports_running_when_port_listening() -> None:
    port = _free_port()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    accept_thread = threading.Thread(target=lambda: _try_accept(srv), daemon=True)
    accept_thread.start()
    try:
        probe = McpStatusProbe(host="127.0.0.1", port=port)
        result = probe.check()
    finally:
        srv.close()
    assert result.running is True
    assert result.host == "127.0.0.1"
    assert result.port == port


def _try_accept(srv: socket.socket) -> None:
    try:
        conn, _ = srv.accept()
        conn.close()
    except OSError:
        pass


def test_probe_reports_not_running_when_port_closed() -> None:
    port = _free_port()  # nothing listening here
    probe = McpStatusProbe(host="127.0.0.1", port=port)
    result = probe.check()
    assert result.running is False


def test_mcp_status_does_not_claim_not_running_when_listening() -> None:
    port = _free_port()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    threading.Thread(target=lambda: _try_accept(srv), daemon=True).start()

    listen_port = port

    class _FakeMcpSettings:
        enabled = True
        host = "127.0.0.1"
        port = listen_port
        transport = "sse"

    class _FakeSettings:
        mcp_server = _FakeMcpSettings()

    try:
        with patch("stackowl.config.settings.Settings", return_value=_FakeSettings()):
            result = runner.invoke(app, ["mcp", "status"])
    finally:
        srv.close()

    assert result.exit_code == 0, result.output
    assert "not running" not in result.output.lower()
    assert "running" in result.output.lower()
    assert str(port) in result.output


def test_mcp_list_clients_does_not_fabricate_empty_client_list() -> None:
    class _FakeMcpSettings:
        enabled = False
        host = "127.0.0.1"
        port = 8765
        transport = "sse"

    class _FakeSettings:
        mcp_server = _FakeMcpSettings()

    with patch("stackowl.config.settings.Settings", return_value=_FakeSettings()):
        result = runner.invoke(app, ["mcp", "list-clients"])

    assert result.exit_code == 0, result.output
    # Must NOT assert a definitive empty client list it cannot actually observe.
    assert "No active MCP clients" not in result.output
