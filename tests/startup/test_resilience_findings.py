"""S2 boot/restart resilience findings — F-36 (respawn reconnect), F-86 (census
boot phase), F-88 (mono service-manager detection)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import stackowl.startup.orchestrator as orch
from stackowl.startup.orchestrator import (
    StartupOrchestrator,
    detect_service_manager,
)


# --------------------------------------------------------------------------- #
# F-88 — service manager detection
# --------------------------------------------------------------------------- #
def test_detect_none_when_bare_shell() -> None:
    assert detect_service_manager({}, "linux") is None


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"INVOCATION_ID": "abc"}, "systemd"),
        ({"NOTIFY_SOCKET": "/run/systemd/notify"}, "systemd"),
        ({"WATCHDOG_USEC": "30000000"}, "systemd"),
        ({"SUPERVISOR_ENABLED": "1"}, "supervisord"),
        ({"PM2_HOME": "/home/u/.pm2"}, "pm2"),
        ({"pm_id": "0"}, "pm2"),
        ({"S6_SERVICE_PATH": "/run/s6"}, "runit/s6"),
    ],
)
def test_detect_known_managers(env: dict[str, str], expected: str) -> None:
    assert detect_service_manager(env, "linux") == expected


def test_detect_launchd_on_darwin() -> None:
    assert detect_service_manager({"XPC_SERVICE_NAME": "com.x.owl"}, "darwin") == "launchd"
    # launchd sets XPC_SERVICE_NAME="0" for non-managed processes → not detected.
    assert detect_service_manager({"XPC_SERVICE_NAME": "0"}, "darwin") is None


# --------------------------------------------------------------------------- #
# F-86 — reachability census runs as a boot phase, never refuses boot
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_census_phase_runs_without_raising() -> None:
    """The advisory census phase must complete on the real default path."""
    orch_inst = StartupOrchestrator()
    await orch_inst._phase_reachability_census()  # must not raise


@pytest.mark.asyncio
async def test_census_phase_warns_loud_on_dead_subsystem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dead probe makes the phase emit a LOUD degraded alert (but still no raise)."""
    from stackowl.health.reachability import ProbeResult, census, reachability_probe

    @reachability_probe("test.f86_dead")
    async def _dead() -> ProbeResult:
        return ProbeResult("test.f86_dead", reachable=False, detail="simulated dead")

    try:
        with caplog.at_level(logging.ERROR, logger="stackowl.startup"):
            await StartupOrchestrator()._phase_reachability_census()
        assert any("CENSUS DEGRADED" in r.message for r in caplog.records)
    finally:
        census._PROBES.pop("test.f86_dead", None)


# --------------------------------------------------------------------------- #
# F-36 — respawned core that never reconnects must not buffer forever
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, rc: int, *, hang: bool = False) -> None:
        self._rc = rc
        self._hang = hang

    async def wait(self) -> int:
        if self._hang:
            await asyncio.Event().wait()  # booted but never exits
        return self._rc


@pytest.mark.asyncio
async def test_respawn_reconnect_timeout_stops_buffering(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(orch, "_CORE_BOOT_TIMEOUT_S", 0.05)

    async def _fake_spawn(socket_path: object, **_kw: object) -> _FakeProc:
        return _FakeProc(0, hang=True)  # the respawned core boots but never connects

    monkeypatch.setattr("stackowl.runtime.supervisor.spawn_core", _fake_spawn)

    proc_holder: dict[str, object] = {"proc": _FakeProc(1)}  # first core crashes
    stop_event = asyncio.Event()
    first_conn = asyncio.Event()  # never set → reconnect times out

    with caplog.at_level(logging.ERROR, logger="stackowl.startup"):
        await asyncio.wait_for(
            orch._supervise_core(
                proc_holder, Path("/tmp/owl.sock"), stop_event, first_conn  # type: ignore[arg-type]
            ),
            timeout=10,
        )

    assert stop_event.is_set(), "gateway must stop buffering on respawn-reconnect timeout"
    assert any("DID NOT RECONNECT" in r.message for r in caplog.records)


class _ReleasableProc:
    """A proc whose wait() blocks until ``release`` is set, then returns rc."""

    def __init__(self, release: asyncio.Event, rc: int = 0) -> None:
        self._release = release
        self._rc = rc

    async def wait(self) -> int:
        await self._release.wait()
        return self._rc


@pytest.mark.asyncio
async def test_respawn_reconnects_successfully_no_false_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A respawned core that DOES reconnect must NOT trip the failure path: the
    supervisor clears the stale event, waits, sees the genuine reconnect, and
    proceeds without setting stop_event."""
    monkeypatch.setattr(orch, "_CORE_BOOT_TIMEOUT_S", 2.0)

    stop_event = asyncio.Event()
    first_conn = asyncio.Event()
    first_conn.set()  # STALE set from the dead core — must be cleared, not honoured
    release = asyncio.Event()
    second = _ReleasableProc(release)

    async def _fake_spawn(socket_path: object, **_kw: object) -> _ReleasableProc:
        # Simulate the accept handler connecting shortly after the spawn.
        asyncio.get_running_loop().call_later(0.05, first_conn.set)
        return second

    monkeypatch.setattr("stackowl.runtime.supervisor.spawn_core", _fake_spawn)

    proc_holder: dict[str, object] = {"proc": _FakeProc(1)}  # first core crashes

    task = asyncio.create_task(
        orch._supervise_core(
            proc_holder, Path("/tmp/owl.sock"), stop_event, first_conn  # type: ignore[arg-type]
        )
    )
    # Wait until the respawn has been adopted (backoff is ~1s).
    for _ in range(200):
        if proc_holder["proc"] is second:
            break
        await asyncio.sleep(0.05)

    assert proc_holder["proc"] is second, "respawned core was not adopted"
    assert not stop_event.is_set(), "successful reconnect must not trip the failure path"

    # Tear the loop down cleanly.
    stop_event.set()
    release.set()
    await asyncio.wait_for(task, timeout=10)
