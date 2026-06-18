"""F144 + F142 — gateway cooperative shutdown + watchdog wiring.

F144: the old ``_register_pid_cleanup._cleanup`` raised ``SystemExit(0)`` from a
sync signal handler, which does not reliably unwind the awaited ``adapter.run()`` —
so the real teardown in the ``_phase_gateway`` ``finally`` (process_registry.clear_all,
db_pool.close, durable_recoverer.drain, PID removal) was skipped, orphaning child
processes and leaking the DB handle.

These tests drive the REAL ``_run_until_signal`` race helper and assert the AST
wiring of ``_phase_gateway`` (cooperative path, watchdog wired, not a hard exit).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

import pytest

from stackowl.startup import orchestrator as orch_mod
from stackowl.startup.orchestrator import _run_until_signal


# =========================================================================== #
# 1. _run_until_signal race helper — the structural core of the fix
# =========================================================================== #


@pytest.mark.asyncio
async def test_stop_event_unwinds_a_forever_adapter() -> None:
    """When the stop_event fires, the adapter task is cancelled and the helper
    returns cleanly — the caller's ``finally`` then runs the real teardown."""
    started = asyncio.Event()
    cancelled = {"v": False}

    class _Adapter:
        async def run(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()  # never completes on its own
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise

    stop_event = asyncio.Event()

    async def _trip() -> None:
        await started.wait()
        stop_event.set()

    trip = asyncio.create_task(_trip())
    await asyncio.wait_for(_run_until_signal(_Adapter(), stop_event), timeout=2.0)
    await trip

    assert cancelled["v"] is True  # the forever adapter was actually cancelled


@pytest.mark.asyncio
async def test_adapter_finishing_first_does_not_hang_on_stop_event() -> None:
    """If the adapter returns on its own (e.g. CLI user quits), the helper
    returns without waiting on the never-set stop_event."""

    class _Adapter:
        async def run(self) -> None:
            await asyncio.sleep(0)  # returns immediately

    stop_event = asyncio.Event()  # never set
    await asyncio.wait_for(_run_until_signal(_Adapter(), stop_event), timeout=2.0)


# =========================================================================== #
# 2. AST wiring — _phase_gateway uses the cooperative path (merge-gate)
# =========================================================================== #


def _gateway_src() -> str:
    return textwrap.dedent(inspect.getsource(orch_mod.StartupOrchestrator._phase_gateway))


def test_gateway_uses_run_until_signal_not_bare_adapter_run() -> None:
    """The blocking call is ``_run_until_signal(...)``; a bare ``await adapter.run()``
    in the gateway body would mean a signal can never reach the teardown."""
    src = _gateway_src()
    assert "_run_until_signal" in src, "gateway must race the adapter against a stop_event"


def test_gateway_registers_a_cooperative_signal_handler() -> None:
    """The gateway installs an asyncio signal handler that sets the stop_event
    (POSIX), with a no-SystemExit Windows fallback."""
    src = _gateway_src()
    assert "add_signal_handler" in src
    assert "NotImplementedError" in src  # Windows fallback branch present


def test_gateway_wires_the_real_watchdog_service() -> None:
    """F142: the gateway constructs the real ``WatchdogService`` (start + stop in
    the finally) and sends READY once — not the dead one-shot stub."""
    src = _gateway_src()
    assert "WatchdogService" in src
    assert ".start()" in src
    assert "send_ready" in src


def test_pid_cleanup_handler_no_longer_raises_systemexit() -> None:
    """The legacy signal handler raised SystemExit, bypassing the async finally.
    The rewritten ``_register_pid_cleanup`` must not raise SystemExit anymore."""
    src = textwrap.dedent(inspect.getsource(orch_mod.StartupOrchestrator._register_pid_cleanup))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            assert not (
                isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name)
                and node.exc.func.id == "SystemExit"
            ), "signal handler must not hard-exit (F144)"


def test_dead_watchdog_stub_is_gone() -> None:
    """F142: the old ``startup.watchdog`` stub (WatchdogSec/KeepAlive) is deleted;
    the orchestrator no longer imports it."""
    src = inspect.getsource(orch_mod)
    assert "WatchdogSec" not in src
    assert "from stackowl.startup.watchdog import" not in src
