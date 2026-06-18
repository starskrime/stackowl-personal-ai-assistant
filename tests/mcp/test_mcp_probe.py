"""Tests for McpLivenessProbe."""

from __future__ import annotations

import pytest

from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from stackowl.mcp.allowlist import McpServerConfig
from stackowl.mcp.probe import McpLivenessProbe


def _cfg(name: str = "srv", uri: str = "sse://http://localhost:9999/sse") -> McpServerConfig:
    return McpServerConfig(name=name, uri=uri, timeout_seconds=1.0)


@pytest.mark.asyncio
async def test_probe_one_raises_in_test_mode() -> None:
    probe = McpLivenessProbe()
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await probe.probe_one(_cfg())
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_probe_all_raises_in_test_mode() -> None:
    probe = McpLivenessProbe()
    TestModeGuard.activate()
    try:
        with pytest.raises(TestModeViolation):
            await probe.probe_all([_cfg()])
    finally:
        TestModeGuard.deactivate()


@pytest.mark.asyncio
async def test_probe_all_returns_dict_keyed_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = McpLivenessProbe()

    async def fake_probe_one(config: McpServerConfig) -> bool:
        return True

    monkeypatch.setattr(probe, "probe_one", fake_probe_one)
    result = await probe.probe_all([_cfg("a"), _cfg("b")])
    assert set(result.keys()) == {"a", "b"}


@pytest.mark.asyncio
async def test_probe_one_returns_false_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    probe = McpLivenessProbe()

    async def raise_connect(*a: object, **kw: object) -> object:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", raise_connect)  # type: ignore[attr-defined]
    result = await probe.probe_one(_cfg())
    assert result is False


@pytest.mark.asyncio
async def test_probe_one_returns_false_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import httpx
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    probe = McpLivenessProbe()

    async def raise_timeout(*a: object, **kw: object) -> object:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(httpx.AsyncClient, "get", raise_timeout)  # type: ignore[attr-defined]
    result = await probe.probe_one(_cfg())
    assert result is False


@pytest.mark.asyncio
async def test_probe_one_never_raises_on_unknown_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", lambda op: None)
    probe = McpLivenessProbe()

    async def boom(*a: object, **kw: object) -> object:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)  # type: ignore[attr-defined]
    result = await probe.probe_one(_cfg())  # must not raise
    assert isinstance(result, bool)
