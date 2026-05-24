"""Tests for McpServerAllowlist and McpServerConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.mcp.allowlist import McpServerAllowlist, McpServerConfig


def test_is_allowed_matching_prefix() -> None:
    al = McpServerAllowlist(["http://localhost:"])
    assert al.is_allowed("http://localhost:8080/sse") is True


def test_is_allowed_no_match() -> None:
    al = McpServerAllowlist(["http://localhost:"])
    assert al.is_allowed("http://remote.example.com/sse") is False


def test_is_allowed_empty_denies_all() -> None:
    al = McpServerAllowlist([])
    assert al.is_allowed("http://localhost:8080") is False


def test_is_allowed_stdio_prefix() -> None:
    al = McpServerAllowlist(["stdio://"])
    assert al.is_allowed("stdio:///usr/local/bin/myserver") is True


def test_is_allowed_multiple_prefixes() -> None:
    al = McpServerAllowlist(["http://localhost:", "stdio://"])
    assert al.is_allowed("stdio:///path/to/server") is True
    assert al.is_allowed("http://localhost:9000/sse") is True
    assert al.is_allowed("http://remote:9000/sse") is False


def test_add_prefix_at_runtime() -> None:
    al = McpServerAllowlist(["http://localhost:"])
    assert al.is_allowed("http://trusted.internal:8080") is False
    al.add("http://trusted.internal:")
    assert al.is_allowed("http://trusted.internal:8080") is True


def test_server_config_is_frozen() -> None:
    cfg = McpServerConfig(name="test", uri="sse://http://localhost:8765/sse")
    with pytest.raises((TypeError, ValidationError)):
        cfg.name = "other"  # type: ignore[misc]


def test_server_config_default_timeout() -> None:
    cfg = McpServerConfig(name="fs", uri="stdio:///bin/fs-server")
    assert cfg.timeout_seconds == 3.0


def test_server_config_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        McpServerConfig(name="x", uri="stdio://x", unknown_field=True)  # type: ignore[call-arg]
