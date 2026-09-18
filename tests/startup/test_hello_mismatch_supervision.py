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

from stackowl.db.pool import DbPool
from stackowl.journal import write_gate
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


class TestTheStanddownIsDurablyJournaled:
    """Story 3.1 (AD-28) -- ``db_pool`` (when passed) journals the stand-down
    as ``link.hello_mismatch_standdown`` right before the critical log +
    return, opening an `incident`-kind Needs-you item."""

    @pytest.fixture(autouse=True)
    def _reset_write_gate(self) -> object:
        write_gate.reset_for_tests()
        yield None
        write_gate.reset_for_tests()

    async def test_a_wired_db_pool_records_the_standdown_and_opens_an_incident(
        self, tmp_db: DbPool,
    ) -> None:
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
            db_pool=tmp_db,
        )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'link.hello_mismatch_standdown'",
            (),
        )
        assert len(rows) == 1

        item_rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE dedupe_key = ?",
            ("incident:owner:gateway_core_link",),
        )
        assert len(item_rows) == 1
        assert item_rows[0]["intensity"] == "high"

    async def test_no_db_pool_still_stands_down_byte_identically(self) -> None:
        """``db_pool=None`` (the default) -- exactly the existing tests'
        shape above -- must never crash or change the stand-down outcome."""
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

        assert stop_event.is_set() is False

    async def test_the_standdown_write_lands_under_a_genuinely_paused_write_gate(
        self, tmp_db: DbPool,
    ) -> None:
        """THE bug this story's review-pass amendment exists to fix: every
        real Hello-mismatch stand-down runs with the PROCESS-GLOBAL
        write-gate genuinely paused (``GatewayLink._route`` pauses it on
        every mismatch, resumed only by a LATER compatible Hello, never
        before this stand-down's own 3-consecutive-mismatch threshold
        fires). A bare (non-bypassed) ``journal.record()`` call would raise
        ``JournalWritesPausedError`` here, swallowed by the stand-down's own
        ``except Exception`` -- the row would silently never land. This
        pauses the REAL ``write_gate`` module (not a fake/bypassed one, and
        not a fresh-``tmp_db``-only unit test) before calling
        ``_supervise_core``, and proves the row still lands because
        ``bypass_write_gate=True`` is wired at the one sanctioned call site.
        """
        write_gate.pause_writes("test: simulated real Hello mismatch, unresolved")
        assert write_gate.writes_paused() is True  # sanity: the gate really is paused

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
            db_pool=tmp_db,
        )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'link.hello_mismatch_standdown'",
            (),
        )
        assert len(rows) == 1, (
            "the stand-down write must land even under a genuinely paused write-gate"
        )

        # The fix must NEVER call write_gate.resume_writes() as a workaround
        # (Boundaries: that would reopen a real race window for every other
        # concurrent writer in the gateway process) -- the gate stays paused.
        assert write_gate.writes_paused() is True

    async def test_a_failing_journal_write_does_not_stop_the_standdown_from_completing(
        self,
    ) -> None:
        """Review-pass hardening (verification-gap finding): the stand-down's
        own ``try/except`` around its journal write is the only thing
        standing between a journal-side failure and the critical log +
        return never happening. Forces the write to fail via a ``db_pool``
        stub whose ``.transaction()`` raises, and proves ``_supervise_core``
        still completes (does not propagate) rather than the exception
        escaping."""

        class _BoomTransactionDbPool:
            def transaction(self) -> None:
                raise RuntimeError("simulated db_pool.transaction failure")

        proc_holder: dict[str, object] = {"proc": _FakeProc()}
        stop_event = asyncio.Event()
        gateway_link = _FakeGatewayLink(
            consecutive_hello_mismatches=orchestrator._MAX_CONSECUTIVE_HELLO_MISMATCHES
        )

        # Must not raise -- the stand-down (loud critical log + return) must
        # still complete even though the journal write inside it blows up.
        await orchestrator._supervise_core(
            proc_holder,
            socket_path=object(),  # type: ignore[arg-type]
            stop_event=stop_event,
            gateway_link=gateway_link,  # type: ignore[arg-type]
            db_pool=_BoomTransactionDbPool(),  # type: ignore[arg-type]
        )

        assert stop_event.is_set() is False  # standing down != tearing the gateway down
