"""Tests for CamoufoxRuntime self-healing after crash/disconnect."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from stackowl.config.browser import BrowserSettings
from stackowl.tools.browser.runtime import CamoufoxRuntime

pytestmark = pytest.mark.asyncio


class _FakeBrowser:
    """Stub Playwright Browser. Tracks disconnect listeners; on_disconnect() fires them."""

    def __init__(self) -> None:
        self._listeners: list[Callable[[Any], None]] = []
        self.new_context_calls: int = 0
        self._next_new_context_exc: BaseException | None = None

    def on(self, event: str, cb: Callable[[Any], None]) -> None:
        if event == "disconnected":
            self._listeners.append(cb)

    async def new_context(self, **_kwargs: Any) -> "_FakeContext":
        self.new_context_calls += 1
        if self._next_new_context_exc is not None:
            exc = self._next_new_context_exc
            self._next_new_context_exc = None
            raise exc
        return _FakeContext()

    def queue_new_context_failure(self, exc: BaseException) -> None:
        self._next_new_context_exc = exc

    def fire_disconnect(self) -> None:
        for cb in list(self._listeners):
            cb(self)


class _FakeContext:
    async def close(self) -> None:
        pass


class _FakeManager:
    def __init__(self) -> None:
        self.browsers: list[_FakeBrowser] = []
        self.exits: int = 0

    async def __aenter__(self) -> _FakeBrowser:
        b = _FakeBrowser()
        self.browsers.append(b)
        return b

    async def __aexit__(self, *_args: Any) -> None:
        self.exits += 1


def _patched_camoufox(managers: list[_FakeManager]):
    """Return a context manager that replaces AsyncCamoufox with a stub that
    appends a fresh _FakeManager to `managers` on each instantiation."""

    def _factory(**_kwargs: Any) -> _FakeManager:
        m = _FakeManager()
        managers.append(m)
        return m

    # Provide a minimal `camoufox.async_api` module since import in runtime is deferred.
    import sys
    import types

    fake_module = types.ModuleType("camoufox.async_api")
    fake_module.AsyncCamoufox = _factory  # type: ignore[attr-defined]
    fake_pkg = types.ModuleType("camoufox")
    sys.modules.setdefault("camoufox", fake_pkg)
    sys.modules["camoufox.async_api"] = fake_module
    return patch.dict(sys.modules, {"camoufox.async_api": fake_module}, clear=False)


@pytest.fixture
def settings(tmp_path: Any) -> BrowserSettings:
    return BrowserSettings(
        headless_mode="true",
        humanize=False,
        block_images=True,
        block_webrtc=True,
        geoip=False,
        profiles_dir=tmp_path / "profiles",
        screenshots_dir=tmp_path / "shots",
        downloads_dir=tmp_path / "dl",
        browser_cache_dir=tmp_path / "cache",
    )


@pytest.fixture
def _no_test_mode_guard():
    from stackowl.config.test_mode import TestModeGuard

    with patch.object(TestModeGuard, "assert_not_test_mode"):
        yield


async def test_register_on_recycled_appends_callback(settings: BrowserSettings) -> None:
    runtime = CamoufoxRuntime(settings)
    fired: list[int] = []
    runtime.register_on_recycled(lambda: fired.append(1))
    runtime.register_on_recycled(lambda: fired.append(2))
    runtime._fire_on_recycled()
    assert fired == [1, 2]


async def test_mark_disconnected_flips_state_and_fires_callbacks(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        assert runtime.available is True

        fired: list[int] = []
        runtime.register_on_recycled(lambda: fired.append(1))

        runtime._mark_disconnected()

        assert runtime.available is False
        assert runtime._browser is None
        assert runtime.unavailable_reason == "browser process disconnected"
        assert fired == [1]


async def test_disconnect_event_triggers_mark_disconnected(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        browser = managers[0].browsers[0]
        assert runtime.available is True
        browser.fire_disconnect()
        assert runtime.available is False
        assert runtime._browser is None


async def test_ensure_available_restarts_after_disconnect(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        assert runtime.recycle_count == 0

        runtime._mark_disconnected()
        assert runtime.available is False

        await runtime.ensure_available()

        assert runtime.available is True
        assert runtime._browser is not None
        assert len(managers) == 2  # one for start, one for ensure_available restart
        assert runtime.recycle_count == 1
        assert runtime.last_recycle_reason and "ensure_available" in runtime.last_recycle_reason


async def test_ensure_available_is_idempotent_when_alive(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        await runtime.ensure_available()
        await runtime.ensure_available()
        assert len(managers) == 1  # no extra restart


async def test_open_context_recovers_after_dead_handle_error(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        browser = managers[0].browsers[0]
        browser.queue_new_context_failure(
            RuntimeError("Browser.new_context: Connection closed while reading from the driver"),
        )

        ctx = await runtime.open_context(owner_key="local")

        assert isinstance(ctx, _FakeContext)
        # First new_context raised; runtime recycled; second new_context succeeded on fresh browser.
        assert len(managers) == 2
        assert managers[1].browsers[0].new_context_calls == 1


async def test_open_context_propagates_non_dead_handle_errors(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        browser = managers[0].browsers[0]
        browser.queue_new_context_failure(ValueError("invalid context options"))

        with pytest.raises(ValueError, match="invalid context options"):
            await runtime.open_context(owner_key="local")
        # No recycle for non-dead-handle errors.
        assert len(managers) == 1


async def test_recycle_callbacks_fire_on_scheduled_recycle(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()

        fired: list[int] = []
        runtime.register_on_recycled(lambda: fired.append(1))

        # Force a recycle via the internal helper (simulates scheduled handler).
        async with runtime._lock:
            await runtime._recycle_inside_lock(reason="test-scheduled-recycle")

        assert runtime.recycle_count == 1
        assert fired == [1]


async def test_concurrent_ensure_available_only_restarts_once(
    settings: BrowserSettings, _no_test_mode_guard: Any
) -> None:
    managers: list[_FakeManager] = []
    with _patched_camoufox(managers):
        runtime = CamoufoxRuntime(settings)
        await runtime.start()
        runtime._mark_disconnected()

        # Two concurrent callers — only one restart should happen.
        await asyncio.gather(
            runtime.ensure_available(),
            runtime.ensure_available(),
        )
        # One for original start, one for the (single) recovery.
        assert len(managers) == 2
