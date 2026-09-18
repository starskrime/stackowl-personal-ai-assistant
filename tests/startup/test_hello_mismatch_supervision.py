"""Spec 2.3 — repeated gateway/core Hello mismatches stop the respawn loop.

A genuinely-older gateway will fail EVERY fresh core's Hello the same way
(``runtime.hello.evaluate_hello``'s digest-only tiebreak), so
``_supervise_core`` respawning past ``_MAX_CONSECUTIVE_HELLO_MISMATCHES`` is
pure waste — the fix needs an operator to restart the gateway, not another
core respawn. Direct-import pattern mirrors
``tests/startup/test_a_process_that_is_alive_and_not_serving_is_restarted.py``.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.startup import orchestrator


class _FakeGatewayLink:
    """Only the ONE property `_supervise_core` reads."""

    def __init__(self, consecutive_hello_mismatches: int) -> None:
        self.consecutive_hello_mismatches = consecutive_hello_mismatches


class _FakeProc:
    """A core that exits immediately — `_supervise_core`'s loop body needs
    ONE full iteration (wait -> respawn-or-stand-down) to reach the check."""

    def __init__(self) -> None:
        self.pid = 1234

    async def wait(self) -> int:
        return 0


async def test_supervise_core_stops_respawning_at_the_mismatch_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_calls = 0

    async def _fake_spawn_core(socket_path: object, *, env: object = None) -> _FakeProc:
        nonlocal spawn_calls
        spawn_calls += 1
        return _FakeProc()

    monkeypatch.setattr(
        "stackowl.runtime.supervisor.spawn_core", _fake_spawn_core,
    )

    proc_holder: dict[str, object] = {"proc": _FakeProc()}
    stop_event = asyncio.Event()
    gateway_link = _FakeGatewayLink(
        consecutive_hello_mismatches=orchestrator._MAX_CONSECUTIVE_HELLO_MISMATCHES
    )

    await orchestrator._supervise_core(
        proc_holder,
        socket_path=object(),  # type: ignore[arg-type]
        stop_event=stop_event,
        gateway_link=gateway_link,  # type: ignore[arg-type]
    )

    # Stood down WITHOUT ever calling spawn_core again — the mismatch ceiling
    # was already at/above the limit on the very first check.
    assert spawn_calls == 0
    assert stop_event.is_set() is False  # standing down ≠ tearing the gateway down


async def test_supervise_core_keeps_respawning_below_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vacuity control: below the ceiling, the loop still tries to respawn
    (proves the check above is real, not a guard that always fires).

    Spec 2.4 — also asserts the respawn's `env` kwarg carries this boot's
    `link_secret` under `link_auth.ENV_LINK_SECRET`, merged with the ambient
    environment (not a bare override) — the same secret an `os.execv` restart
    inherits unchanged, so a crash-respawned core must present it too."""
    import os

    from stackowl.runtime import link_auth

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))
    monkeypatch.setenv("AN_AMBIENT_VAR_TEST_MARKER", "still-here")

    spawn_calls = 0
    respawned = _FakeProc()
    captured_env: dict[str, str] | None = None

    async def _fake_spawn_core(socket_path: object, *, env: object = None) -> _FakeProc:
        nonlocal spawn_calls, captured_env
        spawn_calls += 1
        captured_env = env  # type: ignore[assignment]
        return respawned

    monkeypatch.setattr(
        "stackowl.runtime.supervisor.spawn_core", _fake_spawn_core,
    )

    proc_holder: dict[str, object] = {"proc": _FakeProc()}
    stop_event = asyncio.Event()
    first_conn_event = asyncio.Event()
    first_conn_event.set()  # the respawned core "reconnects" instantly
    gateway_link = _FakeGatewayLink(
        consecutive_hello_mismatches=orchestrator._MAX_CONSECUTIVE_HELLO_MISMATCHES - 1
    )

    task = asyncio.ensure_future(
        orchestrator._supervise_core(
            proc_holder,
            socket_path=object(),  # type: ignore[arg-type]
            stop_event=stop_event,
            first_conn_event=first_conn_event,
            gateway_link=gateway_link,  # type: ignore[arg-type]
            link_secret="respawn-secret-xyz",
        )
    )
    await real_sleep(0.05)
    stop_event.set()  # end the loop for the test
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task

    assert spawn_calls >= 1
    assert captured_env is not None
    assert captured_env[link_auth.ENV_LINK_SECRET] == "respawn-secret-xyz"
    # Merged with the ambient environment, not a bare override.
    assert captured_env["AN_AMBIENT_VAR_TEST_MARKER"] == "still-here"
    assert captured_env.get("PATH") == os.environ.get("PATH")


async def test_the_check_never_fires_when_no_gateway_link_is_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gateway_link=None` (mono/core role, or any existing caller/test that
    predates this story) must be byte-identical to before — no crash, no
    spurious stand-down."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))

    spawn_calls = 0
    respawned = _FakeProc()

    async def _fake_spawn_core(socket_path: object, *, env: object = None) -> _FakeProc:
        nonlocal spawn_calls
        spawn_calls += 1
        return respawned

    monkeypatch.setattr(
        "stackowl.runtime.supervisor.spawn_core", _fake_spawn_core,
    )

    proc_holder: dict[str, object] = {"proc": _FakeProc()}
    stop_event = asyncio.Event()
    first_conn_event = asyncio.Event()
    first_conn_event.set()

    task = asyncio.ensure_future(
        orchestrator._supervise_core(
            proc_holder,
            socket_path=object(),  # type: ignore[arg-type]
            stop_event=stop_event,
            first_conn_event=first_conn_event,
            gateway_link=None,
        )
    )
    await real_sleep(0.05)
    stop_event.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task

    assert spawn_calls >= 1
