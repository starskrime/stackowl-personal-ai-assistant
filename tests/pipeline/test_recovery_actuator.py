"""ADR-2 — RecoveryActuator: one bounded ladder any failing op hands a failure to.

The invariant: a recoverable failure is never surrendered before the ladder is exhausted,
and a "recovered" result is itself re-verified (ADR-1). A CONSEQUENTIAL failure is NEVER
auto-retried — it goes straight to reroute/substitute/surrender. Classification reuses the
project's transient vocabulary (DEFAULT_DEAD_HANDLE_MARKERS) — no new keyword list.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.recovery_actuator import (
    Failure,
    RecoveryActuator,
    classify_tool_failure,
    is_transient_result,
)
from stackowl.tools.base import ToolResult


def _tr(success: bool, *, output: str = "", error: str | None = None,
        verified: bool | None = None) -> ToolResult:
    return ToolResult(
        success=success, output=output, error=error, verified=verified, duration_ms=1.0
    )


# --- classification reuses the transient vocabulary (no new word list) ---------


def test_is_transient_result_uses_dead_handle_markers() -> None:
    assert is_transient_result(_tr(False, error="Connection refused")) is True
    assert is_transient_result(_tr(False, error="database is locked")) is True
    # A deterministic failure (bad input) is NOT transient.
    assert is_transient_result(_tr(False, error="invalid argument: missing url")) is False


def test_classify_derives_failure_shape() -> None:
    f = classify_tool_failure(
        _tr(False, error="Broken pipe"), name="web_fetch",
        consequential=False, capability_tag="web_knowledge",
    )
    assert f.transient is True
    assert f.consequential is False
    assert f.capability_tag == "web_knowledge"
    # Unverified effect: success but reality refuted it.
    u = classify_tool_failure(
        _tr(True, output="done", verified=False), name="write_file",
        consequential=False,
    )
    assert u.unverified_effect is True


# --- should_retry: consequential is NEVER auto-retried -------------------------


def test_should_retry_matrix() -> None:
    act = RecoveryActuator()
    transient = Failure(name="t", transient=True, consequential=False)
    assert act.should_retry(transient) is True
    # Consequential transient → NO auto-retry (the hard guard).
    cons = Failure(name="c", transient=True, consequential=True)
    assert act.should_retry(cons) is False
    # Deterministic non-transient → no retry.
    deterministic = Failure(name="d", transient=False, consequential=False)
    assert act.should_retry(deterministic) is False
    # Unverified effect → retry (claimed success, reality refuted).
    unverified = Failure(name="u", transient=False, unverified_effect=True, consequential=False)
    assert act.should_retry(unverified) is True


# --- the bounded ladder re-verifies each rung ----------------------------------


@pytest.mark.asyncio
async def test_retry_rung_recovers_and_reverifies() -> None:
    act = RecoveryActuator()
    calls = {"n": 0}

    async def attempt() -> str:
        calls["n"] += 1
        return "ok-after-retry"

    f = Failure(name="t", transient=True, consequential=False)
    outcome = await act.recover(f, attempt, verify=lambda r: r == "ok-after-retry")
    assert outcome.recovered is True
    assert outcome.via == "retry"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_consequential_failure_never_auto_retried() -> None:
    act = RecoveryActuator()
    attempted = {"n": 0}

    async def attempt() -> str:
        attempted["n"] += 1
        return "should-not-run"

    rerouted = {"n": 0}

    async def reroute() -> str:
        rerouted["n"] += 1
        return "rerouted-ok"

    f = Failure(name="c", transient=True, consequential=True)
    outcome = await act.recover(f, attempt, reroute=reroute, verify=lambda r: True)
    assert attempted["n"] == 0  # attempt NEVER re-run for a consequential failure
    assert outcome.recovered is True
    assert outcome.via == "reroute"
    assert rerouted["n"] == 1


@pytest.mark.asyncio
async def test_ladder_falls_through_to_surrender() -> None:
    act = RecoveryActuator()

    async def attempt() -> str:
        return "still-bad"

    async def substitute() -> str:
        return "sibling-also-bad"

    f = Failure(name="t", transient=True, consequential=False)
    # Nothing verifies → exhaust the ladder → honest surrender (recovered=False).
    outcome = await act.recover(
        f, attempt, substitute=substitute, verify=lambda r: False
    )
    assert outcome.recovered is False
    assert outcome.via == "surrender"


@pytest.mark.asyncio
async def test_recovered_result_must_pass_verification() -> None:
    act = RecoveryActuator()

    async def attempt() -> str:
        return "unverified"

    async def reroute() -> str:
        return "verified-good"

    f = Failure(name="t", transient=True, consequential=False)
    # retry returns an UNVERIFIED result → not accepted → reroute returns a verified one.
    outcome = await act.recover(
        f, attempt, reroute=reroute, verify=lambda r: r == "verified-good"
    )
    assert outcome.recovered is True
    assert outcome.via == "reroute"


@pytest.mark.asyncio
async def test_attempt_that_raises_is_contained() -> None:
    act = RecoveryActuator()

    async def attempt() -> str:
        raise RuntimeError("boom")

    f = Failure(name="t", transient=True, consequential=False)
    # A raising rung must not propagate — it advances the ladder (here → surrender).
    outcome = await act.recover(f, attempt, verify=lambda r: True)
    assert outcome.recovered is False
    assert outcome.via == "surrender"
