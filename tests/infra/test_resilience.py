"""Tests for the shared HealableResource protocol + retry_once_on_dead_handle."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from stackowl.infra.resilience import (
    HealableResource,
    looks_like_dead_handle,
    retry_once_on_dead_handle,
)


class _FakeResource:
    """Minimal HealableResource that counts ensure_available() calls."""

    def __init__(self, recover: bool = True) -> None:
        self._available = True
        self.ensure_calls = 0
        self.recycle_cbs: list[Callable[[], None]] = []
        self._recover = recover

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return None if self._available else "stub: marked dead"

    async def ensure_available(self) -> None:
        self.ensure_calls += 1
        if self._recover:
            self._available = True
        else:
            raise RuntimeError("could not recover")

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        self.recycle_cbs.append(cb)


def test_protocol_runtime_checkable() -> None:
    assert isinstance(_FakeResource(), HealableResource)


def test_looks_like_dead_handle_recognises_default_markers() -> None:
    assert looks_like_dead_handle(Exception("Connection closed while reading from the driver"))
    assert looks_like_dead_handle(Exception("Browser.new_context: Target closed"))
    assert looks_like_dead_handle(Exception("sqlite3.OperationalError: database is locked"))
    assert looks_like_dead_handle(Exception("BrokenPipeError: [Errno 32] Broken pipe"))
    assert looks_like_dead_handle(Exception("ServerDisconnectedError"))


def test_looks_like_dead_handle_rejects_other_errors() -> None:
    assert not looks_like_dead_handle(Exception("ValueError: bad input"))
    assert not looks_like_dead_handle(Exception("TimeoutError: timed out"))
    assert not looks_like_dead_handle(Exception("404 not found"))


async def test_retry_once_returns_value_on_first_success() -> None:
    res = _FakeResource()
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    out = await retry_once_on_dead_handle(op, res, op_name="t")
    assert out == "ok"
    assert calls == 1
    assert res.ensure_calls == 0


async def test_retry_once_recycles_and_retries_on_dead_handle() -> None:
    res = _FakeResource()
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Connection closed mid-flight")
        return "ok"

    out = await retry_once_on_dead_handle(op, res, op_name="t")
    assert out == "ok"
    assert calls == 2
    assert res.ensure_calls == 1


async def test_retry_once_propagates_non_dead_handle_errors_without_retry() -> None:
    res = _FakeResource()
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        await retry_once_on_dead_handle(op, res, op_name="t")
    assert calls == 1
    assert res.ensure_calls == 0


async def test_retry_once_propagates_second_failure() -> None:
    res = _FakeResource()
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("Connection closed")

    with pytest.raises(RuntimeError, match="Connection closed"):
        await retry_once_on_dead_handle(op, res, op_name="t")
    assert calls == 2
    assert res.ensure_calls == 1


async def test_retry_once_propagates_ensure_failure() -> None:
    res = _FakeResource(recover=False)
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("Connection closed")

    with pytest.raises(RuntimeError, match="could not recover"):
        await retry_once_on_dead_handle(op, res, op_name="t")
    assert calls == 1
    assert res.ensure_calls == 1


async def test_retry_once_respects_custom_dead_markers() -> None:
    res = _FakeResource()
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("CUSTOM_DEAD_TOKEN")
        return "ok"

    out = await retry_once_on_dead_handle(
        op, res, op_name="t", dead_markers=("CUSTOM_DEAD_TOKEN",)
    )
    assert out == "ok"
    assert calls == 2
