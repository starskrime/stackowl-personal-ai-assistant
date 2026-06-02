"""Unit tests for SandboxGovernor (E11-S6) — the global concurrency cap.

The governor bounds total concurrent sandbox runs so N runs × the per-run memory
cap cannot OOM the host. These tests prove the load-bearing invariants WITHOUT
spawning any real sandbox: slots acquire up to N, the (N+1)th past the bounded wait
REFUSES with a typed error (never deadlocks), release frees a slot, the in-flight
count is accurate, and odd usage never raises a leak.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.sandbox.governor import (
    SandboxGovernor,
    SandboxSaturatedError,
    run_under_slot,
)
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps


def test_rejects_nonpositive_max() -> None:
    with pytest.raises(ValueError):
        SandboxGovernor(0)
    with pytest.raises(ValueError):
        SandboxGovernor(-1)


async def test_slot_acquires_up_to_n() -> None:
    gov = SandboxGovernor(2)
    assert gov.in_flight == 0
    async with gov.slot():
        assert gov.in_flight == 1
        async with gov.slot():
            assert gov.in_flight == 2  # both permits held
    assert gov.in_flight == 0  # both released


async def test_nth_plus_one_refuses_past_bounded_wait_never_deadlocks() -> None:
    gov = SandboxGovernor(1)
    async with gov.slot():  # hold the only permit
        # The 2nd acquire must REFUSE within the bounded timeout, not block forever.
        with pytest.raises(SandboxSaturatedError):
            async with gov.slot(timeout=0.05):
                pytest.fail("must never acquire a slot while saturated")
    # The whole test returning proves it did not deadlock.
    assert gov.in_flight == 0


async def test_release_frees_a_slot() -> None:
    gov = SandboxGovernor(1)
    async with gov.slot():
        assert gov.in_flight == 1
    # After release the next acquire succeeds immediately (no refusal).
    async with gov.slot(timeout=0.05):
        assert gov.in_flight == 1
    assert gov.in_flight == 0


async def test_refusal_does_not_consume_a_permit() -> None:
    gov = SandboxGovernor(1)
    async with gov.slot():
        with pytest.raises(SandboxSaturatedError):
            async with gov.slot(timeout=0.02):
                pass
        assert gov.in_flight == 1  # still just the one holder; refusal leaked nothing
    # And the permit is fully back afterwards.
    async with gov.slot(timeout=0.05):
        assert gov.in_flight == 1


async def test_concurrent_acquire_release_is_correct() -> None:
    gov = SandboxGovernor(3)
    peak = 0

    async def worker() -> None:
        nonlocal peak
        async with gov.slot(timeout=2.0):
            peak = max(peak, gov.in_flight)
            await asyncio.sleep(0.01)

    await asyncio.gather(*(worker() for _ in range(3)))
    assert peak <= 3  # never exceeded the cap
    assert gov.in_flight == 0  # all released


async def test_slot_releases_on_exception_never_leaks() -> None:
    gov = SandboxGovernor(1)
    with pytest.raises(RuntimeError):
        async with gov.slot():
            raise RuntimeError("boom inside slot")
    assert gov.in_flight == 0  # permit returned despite the exception
    async with gov.slot(timeout=0.05):  # proves it is acquirable again
        assert gov.in_flight == 1


async def test_slot_releases_on_cancel_never_leaks() -> None:
    gov = SandboxGovernor(1)

    async def holder(started: asyncio.Event) -> None:
        async with gov.slot():
            started.set()
            await asyncio.sleep(100)  # cancelled while holding the permit

    started = asyncio.Event()
    task = asyncio.create_task(holder(started))
    await started.wait()
    assert gov.in_flight == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gov.in_flight == 0  # cancel did not leak the permit


async def test_timeout_none_waits_for_a_freeing_slot() -> None:
    gov = SandboxGovernor(1)

    async def holder(release: asyncio.Event) -> None:
        async with gov.slot():
            await release.wait()

    release = asyncio.Event()
    h = asyncio.create_task(holder(release))
    await asyncio.sleep(0.01)  # let the holder grab the permit

    async def waiter() -> bool:
        async with gov.slot(timeout=None):  # queues until the holder releases
            return True

    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)
    assert not w.done()  # still queued, not refused
    release.set()
    assert await asyncio.wait_for(w, timeout=1.0) is True
    await h
    assert gov.in_flight == 0


# --------------------------------------------------------------- run_under_slot


class _FakeBackend:
    def __init__(self, result: ExecResult) -> None:
        self._result = result
        self.ran = False

    @property
    def name(self) -> str:
        return "fake"

    async def run(self, spec: ExecSpec) -> ExecResult:
        self.ran = True
        return self._result


def _ok() -> ExecResult:
    return ExecResult.ok(
        stdout="ok", stderr="", exit_code=0, backend_used="fake",
        network_enabled=False, caps_applied=ResourceCaps(), duration_ms=1,
    )


def _spec() -> ExecSpec:
    return ExecSpec(code="print(1)", language="python", network=False,
                    caps=ResourceCaps(), session_id="t")


async def test_run_under_slot_none_governor_runs_ungated() -> None:
    backend = _FakeBackend(_ok())
    res = await run_under_slot(None, backend, _spec())  # type: ignore[arg-type]
    assert backend.ran is True
    assert res.exit_reason == "ok"


async def test_run_under_slot_holds_a_slot_for_the_run() -> None:
    gov = SandboxGovernor(1)
    backend = _FakeBackend(_ok())
    res = await run_under_slot(gov, backend, _spec())  # type: ignore[arg-type]
    assert backend.ran is True and res.exit_reason == "ok"
    assert gov.in_flight == 0  # released after the run


async def test_run_under_slot_saturated_never_invokes_backend() -> None:
    gov = SandboxGovernor(1)
    backend = _FakeBackend(_ok())
    async with gov.slot():  # saturate
        with pytest.raises(SandboxSaturatedError):
            await run_under_slot(gov, backend, _spec(),  # type: ignore[arg-type]
                                 )
    assert backend.ran is False  # the backend was NEVER reached — nothing ran
