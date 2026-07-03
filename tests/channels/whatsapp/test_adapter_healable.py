"""Tests for WhatsAppChannelAdapter HealableResource protocol implementation
(ADR-6 Task 7).

Tests the self-healing protocol: available/unavailable_reason/ensure_available/
register_on_recycled/contributor_name. Task 7's bar is higher than the other
channel healers: ensure_available() must perform a REAL browser-driver restart
(stop the OLD WhatsAppBrowserDriver, construct+start a FRESH one) rather than
the WRONG placeholder a prior task in this arc shipped and had to revert
(restarting only the poll TASK around a still-dead browser). Every "heals"
test below asserts the driver INSTANCE identity actually changed — not just
that ensure_available() didn't raise.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter
from stackowl.channels.whatsapp.browser import WhatsAppBrowserDriver
from stackowl.channels.whatsapp.settings import WhatsAppSettings


def _settings() -> WhatsAppSettings:
    return WhatsAppSettings(
        allowed_phone_numbers=frozenset(["15551234567"]),
        session_dir="/tmp/test_whatsapp_healable_session",
    )


def _adapter() -> WhatsAppChannelAdapter:
    return WhatsAppChannelAdapter(_settings(), data_dir="/tmp/test_whatsapp_healable_data")


def _stub_driver(adapter: WhatsAppChannelAdapter) -> WhatsAppBrowserDriver:
    """Install a stubbed-out driver (start/stop are async no-ops) as the CURRENT
    driver, so ensure_available()'s stop()/start() calls don't touch Playwright
    or trip TestModeGuard.
    """

    async def _noop() -> None:
        return None

    driver = adapter._browser
    driver.start = _noop  # type: ignore[method-assign]
    driver.stop = _noop  # type: ignore[method-assign]
    return driver


@pytest.fixture(autouse=True)
def _stub_fresh_driver_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_available() constructs a FRESH WhatsAppBrowserDriver — stub ITS
    start()/stop() too (every instance constructed during the test, not just the
    adapter's initial one) so no real Playwright/TestModeGuard I/O happens.
    """

    async def _noop_start(self: WhatsAppBrowserDriver) -> None:
        return None

    async def _noop_stop(self: WhatsAppBrowserDriver) -> None:
        return None

    monkeypatch.setattr(WhatsAppBrowserDriver, "start", _noop_start)
    monkeypatch.setattr(WhatsAppBrowserDriver, "stop", _noop_stop)


class TestContributorName:
    def test_contributor_name_is_whatsapp(self) -> None:
        adapter = _adapter()
        assert adapter.contributor_name == "whatsapp"

    async def test_health_check_name_matches_contributor_name(self) -> None:
        adapter = _adapter()
        status = await adapter.health_check()
        assert status.name == adapter.contributor_name == "whatsapp"


class TestHealerKeyMatchesContributorName:
    """Task 1's healer-key drift bug: the healers dict key MUST be read off
    contributor_name dynamically, never hardcoded as a second literal. This test
    reads both sides and asserts they agree — matching Telegram's Task 4 test.
    """

    def test_healer_key_matches_contributor_name(self) -> None:
        adapter = _adapter()
        contributor_name = adapter.contributor_name
        expected_healer_key = "whatsapp"
        assert contributor_name == expected_healer_key


class TestAvailableMirrorsHealthCheck:
    """available/unavailable_reason must mirror health_check()'s existing signal
    (_poll_task is None/done, or no messages polled yet, or stale heartbeat) —
    reused via the shared _health_signal(), never a second invented signal.
    """

    async def test_available_false_when_no_poll_task(self) -> None:
        adapter = _adapter()
        assert adapter._poll_task is None
        assert adapter.available is False
        status = await adapter.health_check()
        assert status.status == "degraded"
        assert adapter.unavailable_reason == status.message

    async def test_available_false_when_poll_task_done(self) -> None:
        adapter = _adapter()
        poll = asyncio.ensure_future(asyncio.sleep(0))
        await asyncio.sleep(0.01)  # let it finish
        adapter._poll_task = poll
        assert poll.done()
        assert adapter.available is False
        status = await adapter.health_check()
        assert status.status == "degraded"
        assert adapter.unavailable_reason == status.message

    async def test_available_false_when_no_messages_polled_yet(self) -> None:
        adapter = _adapter()
        poll = asyncio.ensure_future(asyncio.sleep(60))
        adapter._poll_task = poll
        try:
            assert adapter._last_poll_at is None
            assert adapter.available is False
            status = await adapter.health_check()
            assert status.status == "degraded"
            assert status.message == "no messages polled yet"
            assert adapter.unavailable_reason == status.message
        finally:
            poll.cancel()

    async def test_available_true_when_poll_alive_and_recent(self) -> None:
        adapter = _adapter()
        poll = asyncio.ensure_future(asyncio.sleep(60))
        adapter._poll_task = poll
        try:
            await adapter.handle_message("15551234567@s.whatsapp.net", "hi")
            assert adapter.available is True
            assert adapter.unavailable_reason is None
            status = await adapter.health_check()
            assert status.status == "ok"
        finally:
            poll.cancel()

    async def test_unavailable_reason_stale_heartbeat_matches_health_check(self) -> None:
        adapter = _adapter()
        poll = asyncio.ensure_future(asyncio.sleep(60))
        adapter._poll_task = poll
        try:
            adapter._last_poll_at = time.monotonic() - 120.0
            status = await adapter.health_check()
            assert status.status == "degraded"
            assert adapter.available is False
            assert adapter.unavailable_reason == status.message
        finally:
            poll.cancel()


class TestEnsureAvailableRealBrowserRestart:
    """The core Task 7 bar: ensure_available() must stop the OLD driver and
    swap in a genuinely NEW WhatsAppBrowserDriver instance, not just restart a
    wrapper task around the still-dead one.
    """

    async def test_dead_poll_task_swaps_to_a_new_driver_instance(self) -> None:
        adapter = _adapter()
        old_driver = _stub_driver(adapter)
        # Simulate a crashed poll loop (structurally dead — not just quiet).
        adapter._poll_task = asyncio.ensure_future(asyncio.sleep(0))
        await asyncio.sleep(0.01)
        assert adapter._poll_task.done()
        assert adapter.available is False

        try:
            await adapter.ensure_available()

            # The defining assertion: a genuinely DIFFERENT driver instance now
            # backs the adapter — proves a real rebuild, not a placeholder.
            assert adapter._browser is not old_driver
            assert isinstance(adapter._browser, WhatsAppBrowserDriver)
            # A fresh poll loop was started against the new driver.
            assert adapter._poll_task is not None
            assert not adapter._poll_task.done()
        finally:
            if adapter._poll_task is not None and not adapter._poll_task.done():
                adapter._poll_task.cancel()

    async def test_old_driver_stop_called_before_new_driver_start(self) -> None:
        adapter = _adapter()
        old_driver = adapter._browser
        calls: list[str] = []

        async def _tracked_stop() -> None:
            calls.append("old_stop")

        old_driver.stop = _tracked_stop  # type: ignore[method-assign]

        real_new_start = WhatsAppBrowserDriver.start

        async def _tracked_new_start(self: WhatsAppBrowserDriver) -> None:
            calls.append("new_start")

        import stackowl.channels.whatsapp.adapter as adapter_module

        original_ctor = adapter_module.WhatsAppBrowserDriver

        def _ctor(*args: object, **kwargs: object) -> WhatsAppBrowserDriver:
            driver = original_ctor(*args, **kwargs)  # type: ignore[arg-type]
            driver.start = _tracked_new_start.__get__(driver)  # type: ignore[method-assign]
            return driver

        adapter_module.WhatsAppBrowserDriver = _ctor  # type: ignore[assignment]
        try:
            adapter._poll_task = asyncio.ensure_future(asyncio.sleep(0))
            await asyncio.sleep(0.01)

            await adapter.ensure_available()
        finally:
            adapter_module.WhatsAppBrowserDriver = original_ctor
            if adapter._poll_task is not None and not adapter._poll_task.done():
                adapter._poll_task.cancel()
            _ = real_new_start

        assert calls == ["old_stop", "new_start"]

    async def test_noop_when_already_healthy(self) -> None:
        adapter = _adapter()
        old_driver = _stub_driver(adapter)
        poll = asyncio.ensure_future(asyncio.sleep(60))
        adapter._poll_task = poll
        adapter._last_poll_ok_at = time.monotonic()  # poll-liveness fresh
        try:
            await adapter.handle_message("15551234567@s.whatsapp.net", "hi")
            assert adapter.available is True
            assert adapter._poll_liveness_stale() is False

            await adapter.ensure_available()

            # No rebuild: same driver instance, same poll task.
            assert adapter._browser is old_driver
            assert adapter._poll_task is poll
        finally:
            poll.cancel()

    async def test_stale_poll_liveness_with_alive_poll_task_still_rebuilds(self) -> None:
        """Regression test for the Critical review finding on 0aed6aa8 —
        rewritten in round 3 to drive the rebuild off POLL-LIVENESS
        (``_last_poll_ok_at``), not ``self.available``.

        This is the realistic dead-browser failure mode Task 7 exists to fix:
        ``WhatsAppBrowserDriver.poll_messages()`` swallows its own exceptions
        and returns ``[]`` forever on a crashed/disconnected page, so the
        background poll TASK never dies (``done()`` never becomes True) even
        though the browser itself is dead. Simulated here by setting
        ``last_poll_ok = False`` on the stub driver and staling
        ``_last_poll_ok_at`` directly (standing in for many consecutive real
        poll-tick failures) — a gate keyed on "is the poll task alive" can
        never rebuild in this state, only poll-liveness catches it.
        """
        adapter = _adapter()
        old_driver = _stub_driver(adapter)
        old_driver.last_poll_ok = False  # every real poll tick has been failing
        poll = asyncio.ensure_future(asyncio.sleep(60))
        adapter._poll_task = poll
        adapter._last_poll_ok_at = time.monotonic() - 120.0  # stale poll-liveness
        try:
            assert not poll.done()  # the poll task is ALIVE — the crux of the bug
            assert adapter._poll_liveness_stale() is True  # but the resource is unhealthy

            await adapter.ensure_available()

            # MUST rebuild: a live-but-not-proving-liveness poll task must not
            # mask a dead browser forever.
            assert adapter._browser is not old_driver
            assert isinstance(adapter._browser, WhatsAppBrowserDriver)
            assert adapter._poll_task is not None
            assert not adapter._poll_task.done()
        finally:
            if adapter._poll_task is not None and not adapter._poll_task.done():
                adapter._poll_task.cancel()

    async def test_idle_chat_with_fresh_poll_liveness_does_not_rebuild(self) -> None:
        """THE regression test for the restart-storm bug this round fixes.

        A chat idle >60s is completely normal on a personal-use WhatsApp
        channel: ``_last_poll_at`` goes stale and ``available`` correctly
        flips to False (message-inactivity signal, informational only) — but
        the poll loop itself keeps ticking successfully (browser responds,
        just finds zero messages), so ``_last_poll_ok_at`` stays fresh. This
        MUST NOT trigger a rebuild. Confirmed RED against the pre-round-3 gate
        (``not self.available``, and ``_last_poll_ok_at`` didn't exist at
        all): that code tore down a perfectly healthy browser here. GREEN
        after gating on ``_poll_liveness_stale()``.
        """
        adapter = _adapter()
        old_driver = _stub_driver(adapter)
        poll = asyncio.ensure_future(asyncio.sleep(60))
        adapter._poll_task = poll
        adapter._last_poll_at = time.monotonic() - 120.0  # idle chat >60s
        adapter._last_poll_ok_at = time.monotonic()  # poll ticks keep succeeding
        try:
            assert adapter.available is False  # message-inactivity signal (expected, harmless)
            assert adapter._poll_liveness_stale() is False  # browser is genuinely healthy

            await adapter.ensure_available()

            # MUST NOT rebuild: same driver instance, same poll task.
            assert adapter._browser is old_driver
            assert adapter._poll_task is poll
        finally:
            if adapter._poll_task is not None and not adapter._poll_task.done():
                adapter._poll_task.cancel()

    async def test_poll_liveness_stale_true_when_never_polled(self) -> None:
        """A freshly-constructed adapter (before start()) reports poll-liveness
        stale — there is no successful poll tick yet to prove otherwise.
        """
        adapter = _adapter()
        assert adapter._last_poll_ok_at is None
        assert adapter._poll_liveness_stale() is True

    async def test_start_sets_fresh_poll_liveness_avoiding_boot_race(self) -> None:
        """A just-started adapter must not immediately report poll-liveness
        stale, before the first poll tick has had a chance to run (boot-race).
        """
        adapter = _adapter()
        from stackowl.channels.registry import ChannelRegistry

        ChannelRegistry.instance().reset()
        try:
            await adapter.start()
            assert adapter._last_poll_ok_at is not None
            assert adapter._poll_liveness_stale() is False
        finally:
            if adapter._poll_task is not None and not adapter._poll_task.done():
                adapter._poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await adapter._poll_task
            ChannelRegistry.instance().reset()


class TestRegisterOnRecycled:
    def test_register_on_recycled_is_noop(self) -> None:
        adapter = _adapter()
        called = False

        def callback() -> None:
            nonlocal called
            called = True

        adapter.register_on_recycled(callback)
        assert called is False  # never invoked — no downstream dependents


class TestPollLoopRegressionUnaffected:
    """_poll_loop()'s existing catch-log-continue behavior must be unaffected —
    an exception from poll_messages() still gets logged and the loop keeps
    running (never crashes the task), and it reads self._browser dynamically so
    a driver swap mid-flight is picked up on the next tick.
    """

    async def test_poll_loop_survives_exception_and_keeps_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _adapter()
        call_count = 0

        async def _boom() -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return []

        adapter._browser.poll_messages = _boom  # type: ignore[method-assign]
        monkeypatch.setattr(
            "stackowl.channels.whatsapp.adapter._POLL_INTERVAL_S", 0.01
        )

        task = asyncio.ensure_future(adapter._poll_loop())
        try:
            await asyncio.sleep(0.05)
            assert call_count >= 2, "poll loop must keep ticking after an exception"
            assert not task.done(), "an exception inside the loop must not crash the task"
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_poll_loop_uses_current_browser_reference_after_swap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After ensure_available() swaps self._browser, a freshly-started poll
        loop must call the NEW driver's poll_messages — not a stale captured ref.
        """
        adapter = _adapter()
        old_driver = _stub_driver(adapter)
        old_calls: list[bool] = []

        async def _old_poll() -> list[dict[str, object]]:
            old_calls.append(True)
            return []

        old_driver.poll_messages = _old_poll  # type: ignore[method-assign]

        adapter._poll_task = asyncio.ensure_future(asyncio.sleep(0))
        await asyncio.sleep(0.01)
        monkeypatch.setattr(
            "stackowl.channels.whatsapp.adapter._POLL_INTERVAL_S", 0.01
        )

        await adapter.ensure_available()
        new_calls: list[bool] = []

        async def _new_poll() -> list[dict[str, object]]:
            new_calls.append(True)
            return []

        adapter._browser.poll_messages = _new_poll  # type: ignore[method-assign]

        try:
            await asyncio.sleep(0.05)
            assert new_calls, "the new driver's poll_messages must be invoked"
            assert not old_calls, "the OLD driver must never be polled again"
        finally:
            if adapter._poll_task is not None and not adapter._poll_task.done():
                adapter._poll_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await adapter._poll_task
