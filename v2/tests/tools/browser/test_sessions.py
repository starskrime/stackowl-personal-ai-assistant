"""Tests for BrowserSessionRegistry — TTL, per-owner isolation, hard cap."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from stackowl.config.browser import BrowserSettings
from stackowl.tools.browser.sessions import (
    BrowserSessionLimitError,
    BrowserSessionNotFoundError,
    BrowserSessionRegistry,
)


class _FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url

    async def close(self) -> None:
        pass


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False
        self._pages: list[_FakePage] = []

    async def new_page(self) -> _FakePage:
        p = _FakePage()
        self._pages.append(p)
        return p

    async def close(self) -> None:
        self.closed = True


class _FakeRuntime:
    """Stub CamoufoxRuntime that returns fresh fake contexts on open_context()."""

    def __init__(self) -> None:
        self.opens: list[dict[str, Any]] = []
        self.available = True
        self.recycle_cbs: list[Any] = []

    async def open_context(self, **kwargs: Any) -> _FakeContext:
        self.opens.append(kwargs)
        return _FakeContext()

    def register_on_recycled(self, cb: Any) -> None:
        self.recycle_cbs.append(cb)

    def fire_recycle(self) -> None:
        for cb in self.recycle_cbs:
            cb()


@pytest.fixture
def settings(tmp_path: Any) -> BrowserSettings:
    return BrowserSettings(
        max_concurrent_sessions=3,
        max_concurrent_pages_per_session=2,
        session_idle_timeout_minutes=30,
        profiles_dir=tmp_path / "profiles",
        screenshots_dir=tmp_path / "shots",
        downloads_dir=tmp_path / "dl",
        browser_cache_dir=tmp_path / "cache",
    )


class TestOpenAndClose:
    async def test_open_returns_unique_session_ids(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid1 = await reg.open("alice")
        sid2 = await reg.open("alice")
        assert sid1 != sid2
        assert len(runtime.opens) == 2

    async def test_open_propagates_profile_name(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        await reg.open("alice", profile_name="gmail")
        assert runtime.opens[0]["profile_name"] == "gmail"

    async def test_close_removes_session(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid = await reg.open("alice")
        await reg.close(sid)
        with pytest.raises(BrowserSessionNotFoundError):
            await reg.get(sid)


class TestRuntimeRecyclePurge:
    async def test_runtime_recycle_purges_all_sessions(
        self, settings: BrowserSettings,
    ) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        await reg.open("alice")
        await reg.open("alice")
        await reg.open("bob")
        assert len(reg._sessions) == 3

        runtime.fire_recycle()

        assert len(reg._sessions) == 0
        # New sessions can be opened immediately afterwards.
        sid = await reg.open("alice")
        assert sid in reg._sessions

    async def test_registry_subscribes_during_init(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        assert len(runtime.recycle_cbs) == 1

    async def test_purge_is_idempotent_when_no_sessions(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        runtime.fire_recycle()  # nothing to drop — must not raise
        assert len(reg._sessions) == 0


class TestHardCap:
    async def test_over_cap_raises(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        await reg.open("alice")
        await reg.open("alice")
        await reg.open("bob")
        with pytest.raises(BrowserSessionLimitError):
            await reg.open("carol")


class _BarrierRuntime(_FakeRuntime):
    """open_context awaits a barrier so concurrent opens overlap inside the
    check-then-act window (F155 TOCTOU). ``fail_on`` raises for the Nth open."""

    def __init__(self, gate: asyncio.Event | None = None, fail_on: int = 0) -> None:
        super().__init__()
        self._gate = gate
        self._fail_on = fail_on
        self._n = 0

    async def open_context(self, **kwargs: Any) -> _FakeContext:
        self._n += 1
        n = self._n
        if self._gate is not None:
            await self._gate.wait()
        if n == self._fail_on:
            raise RuntimeError(f"injected open failure #{n}")
        self.opens.append(kwargs)
        return _FakeContext()


class TestConcurrencyCapTOCTOU:
    """F155 — the RACE merge-gate. Serial test_over_cap_raises passes the buggy
    code; only overlapping opens that all clear the cap-read before any insert
    expose it."""

    async def test_concurrent_opens_respect_cap(self, settings: BrowserSettings) -> None:
        gate = asyncio.Event()
        runtime = _BarrierRuntime(gate=gate)
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]

        async def _open(i: int) -> str:
            try:
                await reg.open(f"u{i}")
                return "ok"
            except BrowserSessionLimitError:
                return "refused"

        tasks = [asyncio.create_task(_open(i)) for i in range(8)]  # cap=3
        await asyncio.sleep(0.05)  # let all enter the reserve critical section
        gate.set()
        results = await asyncio.gather(*tasks)

        assert results.count("ok") == 3, f"cap breached: {results}"
        assert results.count("refused") == 5
        assert reg._reserved == 0  # no reservation leak
        assert len(reg._sessions) == 3

    async def test_failed_open_rolls_back_reservation(self, settings: BrowserSettings) -> None:
        runtime = _BarrierRuntime(fail_on=1)
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError):
            await reg.open("alice")  # open_context raises → must propagate, not fake-succeed
        assert reg._reserved == 0  # rolled back — cap not permanently shrunk
        # A subsequent open still succeeds (no leak-DOWN).
        sid = await reg.open("alice")
        assert sid in reg._sessions

    async def test_recycle_during_open_does_not_insert_dead_session(
        self, settings: BrowserSettings,
    ) -> None:
        gate = asyncio.Event()
        runtime = _BarrierRuntime(gate=gate)
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]

        task = asyncio.create_task(reg.open("alice"))
        await asyncio.sleep(0.02)  # parked at the barrier, reservation held, gen captured
        runtime.fire_recycle()  # bumps _recycle_gen mid-open
        gate.set()
        with pytest.raises(BrowserSessionLimitError):  # commit-time gen mismatch → raise loudly
            await task
        assert reg._reserved == 0  # rolled back
        assert len(reg._sessions) == 0  # the dead context was NOT registered


class TestPerOwnerIsolation:
    async def test_list_for_owner_filters(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid_a = await reg.open("telegram:111")
        sid_b1 = await reg.open("telegram:222")
        sid_b2 = await reg.open("telegram:222")

        a_infos = await reg.list_for_owner("telegram:111")
        b_infos = await reg.list_for_owner("telegram:222")

        assert [i.session_id for i in a_infos] == [sid_a]
        assert {i.session_id for i in b_infos} == {sid_b1, sid_b2}

    async def test_close_all_for_owner(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        await reg.open("alice")
        await reg.open("alice")
        await reg.open("bob")
        n = await reg.close_all_for_owner("alice")
        assert n == 2
        # Bob still present.
        assert len(await reg.list_for_owner("bob")) == 1


class TestPageHandles:
    async def test_get_page_creates_new_handle(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid = await reg.open("alice")
        sess, page, handle = await reg.get_page(sid)
        assert page is not None
        assert handle in sess.pages

    async def test_get_page_reuses_existing_handle(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid = await reg.open("alice")
        _, page1, h1 = await reg.get_page(sid)
        _, page2, h2 = await reg.get_page(sid, h1)
        assert h1 == h2
        assert page1 is page2

    async def test_max_pages_per_session_enforced(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid = await reg.open("alice")
        await reg.get_page(sid, None)
        await reg.get_page(sid, None)
        with pytest.raises(BrowserSessionLimitError):
            await reg.get_page(sid, None)


class TestEviction:
    async def test_evict_idle_removes_stale_sessions(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        sid = await reg.open("alice")
        sess = await reg.get(sid)
        # Backdate the session so the TTL check considers it idle.
        sess.last_activity = time.monotonic() - (settings.session_idle_timeout_minutes * 60 + 10)
        evicted = await reg.evict_idle()
        assert evicted == 1
        with pytest.raises(BrowserSessionNotFoundError):
            await reg.get(sid)

    async def test_sweep_loop_lifecycle(self, settings: BrowserSettings) -> None:
        runtime = _FakeRuntime()
        reg = BrowserSessionRegistry(runtime, settings)  # type: ignore[arg-type]
        await reg.start_sweep_loop()
        # Calling start twice is a no-op (no second task spawned).
        await reg.start_sweep_loop()
        await asyncio.sleep(0.01)
        await reg.stop_sweep_loop()
