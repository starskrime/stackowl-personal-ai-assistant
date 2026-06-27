"""F-55 — per-owl transient recovery in nightly evolution.

A crashed/timed-out owl in ``_evolve_one_bounded`` used to be logged and dropped
immediately, so a single network blip / rate-limit silently no-op'd that owl's
evolution until the *next* nightly batch. This adds a bounded retry-once with a
small backoff on TRANSIENT errors (timeout / ``TransientError``) before the owl
is dropped, and surfaces a persistently-stuck owl at WARNING with a clear
``evolution.stuck_owl`` marker. Batch-isolation is preserved: one owl's failure
still never propagates out of ``_evolve_one_bounded``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

import stackowl.owls.evolution as evolution
from stackowl.exceptions import TransientError
from stackowl.owls.evolution import EvolutionCoordinator


@dataclass
class _FakeManifest:
    name: str


def _coord(per_owl_timeout_s: float = 0.05) -> EvolutionCoordinator:
    return EvolutionCoordinator(
        db=object(),  # type: ignore[arg-type]
        provider_registry=object(),  # type: ignore[arg-type]
        owl_registry=object(),  # type: ignore[arg-type]
        per_owl_timeout_s=per_owl_timeout_s,
    )


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the retry path fast and deterministic in tests.
    monkeypatch.setattr(evolution, "_EVOLUTION_RETRY_BACKOFF_SECONDS", 0.0)


@pytest.mark.asyncio
async def test_transient_failure_retried_once_then_succeeds() -> None:
    calls: list[str] = []

    async def flaky(manifest: _FakeManifest) -> bool:
        calls.append(manifest.name)
        if len(calls) == 1:
            raise TransientError("network blip")
        return True

    coord = _coord()
    coord._evolve_one = flaky  # type: ignore[assignment,method-assign]

    result = await coord._evolve_one_bounded(_FakeManifest("a"))  # type: ignore[arg-type]

    assert result is True          # recovered on the retry, owl actually evolved
    assert len(calls) == 2         # one original attempt + exactly one retry


@pytest.mark.asyncio
async def test_persistent_transient_surfaces_stuck_marker_and_isolates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    warnings: list[str] = []

    async def always_down(manifest: _FakeManifest) -> bool:
        calls.append(manifest.name)
        raise TransientError("provider down")

    monkeypatch.setattr(
        evolution.log.engine, "warning",
        lambda msg, *a, **k: warnings.append(str(msg)),
    )

    coord = _coord()
    coord._evolve_one = always_down  # type: ignore[assignment,method-assign]

    result = await coord._evolve_one_bounded(_FakeManifest("b"))  # type: ignore[arg-type]

    assert result is None                       # dropped — never propagated (batch-isolation)
    assert len(calls) == 2                      # retried exactly once before giving up
    assert any("evolution.stuck_owl" in w for w in warnings)  # follow-up marker surfaced


@pytest.mark.asyncio
async def test_timeout_is_treated_as_transient_and_retried() -> None:
    calls: list[str] = []

    async def hangs(manifest: _FakeManifest) -> bool:
        calls.append(manifest.name)
        await asyncio.sleep(10.0)  # far past the per-owl timeout
        return True

    coord = _coord(per_owl_timeout_s=0.02)
    coord._evolve_one = hangs  # type: ignore[assignment,method-assign]

    result = await coord._evolve_one_bounded(_FakeManifest("c"))  # type: ignore[arg-type]

    assert result is None
    assert len(calls) == 2  # timeout is transient → retried once


@pytest.mark.asyncio
async def test_non_transient_crash_is_not_retried() -> None:
    calls: list[str] = []

    async def boom(manifest: _FakeManifest) -> bool:
        calls.append(manifest.name)
        raise RuntimeError("hard bug")  # not transient — must not auto-retry

    coord = _coord()
    coord._evolve_one = boom  # type: ignore[assignment,method-assign]

    result = await coord._evolve_one_bounded(_FakeManifest("d"))  # type: ignore[arg-type]

    assert result is None
    assert len(calls) == 1  # dropped on first failure, no retry
