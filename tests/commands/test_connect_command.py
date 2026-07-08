"""Functional tests for /connect (ConnectCommand._handle_connect).

F-80: success must be CONFIRMED via the adapter's cheap ``is_connected()``
after ``connect()`` returns — a silent OAuth/token-save miss (connect() returns
without raising, yet no credentials persisted) must NOT print "connected".
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.connect_command import ConnectCommand
from stackowl.commands.response import Action, CommandResponse
from stackowl.exceptions import IntegrationNotFoundError
from stackowl.pipeline.state import PipelineState


def _state(session: str = "sess-1") -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_id=session,
        input_text="hello",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


class _FakeAdapter:
    """Minimal stand-in for an IntegrationAdapter."""

    def __init__(
        self,
        service_name: str = "gmail",
        *,
        connected_after: bool = True,
        connect_raises: BaseException | None = None,
    ) -> None:
        self.service_name = service_name
        self._connected_after = connected_after
        self._connect_raises = connect_raises
        self.connect_calls = 0
        self.is_connected_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_raises is not None:
            raise self._connect_raises

    async def is_connected(self) -> bool:
        self.is_connected_calls += 1
        return self._connected_after


class _AdapterNoConfirm:
    """Adapter that lacks is_connected() — must fall back to legacy behavior."""

    def __init__(self, service_name: str = "legacy") -> None:
        self.service_name = service_name
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1


class _FakeRegistry:
    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def get(self, service: str) -> Any:
        if self._adapter is None or service != self._adapter.service_name:
            raise IntegrationNotFoundError(service)
        return self._adapter


class _ListRegistry:
    """Stand-in registry that also supports list_all(), for _handle_list/_menu tests."""

    def __init__(self, adapters: list[Any]) -> None:
        self._adapters = adapters

    def list_all(self) -> list[Any]:
        return self._adapters

    def get(self, service: str) -> Any:
        for a in self._adapters:
            if a.service_name == service:
                return a
        raise IntegrationNotFoundError(service)


@pytest.mark.asyncio
async def test_connect_confirmed_reports_success() -> None:
    adapter = _FakeAdapter("gmail", connected_after=True)
    cmd = ConnectCommand(_FakeRegistry(adapter))
    out = await cmd.handle("gmail", _state())
    assert "connected" in out.lower()
    assert "not" not in out.lower()  # no "not connected" / "not detected"
    assert adapter.connect_calls == 1
    assert adapter.is_connected_calls == 1


@pytest.mark.asyncio
async def test_connect_silent_miss_reports_honest_failure() -> None:
    # connect() returns without raising, but credentials did NOT persist.
    adapter = _FakeAdapter("gmail", connected_after=False)
    cmd = ConnectCommand(_FakeRegistry(adapter))
    out = await cmd.handle("gmail", _state())
    # Must NOT falsely claim success.
    assert out.lower().strip() != "gmail connected."
    assert "not detected" in out.lower() or "not connected" in out.lower()
    assert adapter.connect_calls == 1
    assert adapter.is_connected_calls == 1


@pytest.mark.asyncio
async def test_connect_falls_back_when_no_is_connected() -> None:
    adapter = _AdapterNoConfirm("legacy")
    cmd = ConnectCommand(_FakeRegistry(adapter))
    out = await cmd.handle("legacy", _state())
    # Legacy behavior preserved: success claimed when connect() does not raise.
    assert "connected" in out.lower()
    assert adapter.connect_calls == 1


@pytest.mark.asyncio
async def test_connect_failure_when_connect_raises() -> None:
    adapter = _FakeAdapter("gmail", connect_raises=RuntimeError("oauth boom"))
    cmd = ConnectCommand(_FakeRegistry(adapter))
    out = await cmd.handle("gmail", _state())
    assert "failed to connect" in out.lower()
    assert "oauth boom" in out


# --- bare-list buttons + menu toggle (button-layer UX pattern) ------------

@pytest.mark.asyncio
async def test_bare_list_has_menu_action_per_service() -> None:
    adapter = _FakeAdapter("gmail", connected_after=True)
    cmd = ConnectCommand(_ListRegistry([adapter]))
    out = await cmd.handle("", _state())
    assert isinstance(out, CommandResponse)
    assert out.actions == (
        Action(label="gmail", command="/connect menu gmail", destructive=False),
    )


@pytest.mark.asyncio
async def test_menu_connected_shows_disconnect_toggle() -> None:
    adapter = _FakeAdapter("gmail", connected_after=True)
    cmd = ConnectCommand(_ListRegistry([adapter]))
    out = await cmd.handle("menu gmail", _state())
    assert isinstance(out, CommandResponse)
    assert "connected" in out.text.lower()
    assert out.actions == (
        Action(label="Disconnect", command="/disconnect gmail", destructive=True),
    )


@pytest.mark.asyncio
async def test_menu_disconnected_shows_connect_toggle() -> None:
    adapter = _FakeAdapter("gmail", connected_after=False)
    cmd = ConnectCommand(_ListRegistry([adapter]))
    out = await cmd.handle("menu gmail", _state())
    assert isinstance(out, CommandResponse)
    assert out.actions == (
        Action(label="Connect", command="/connect gmail", destructive=False),
    )


@pytest.mark.asyncio
async def test_menu_unknown_service_returns_plain_error() -> None:
    cmd = ConnectCommand(_ListRegistry([]))
    out = await cmd.handle("menu nope", _state())
    assert not isinstance(out, CommandResponse)
    assert "unknown integration" in out.lower()
