"""LIVE PATH CENSUS — the fail-closed reachability law.

Every consequential default-path subsystem must prove its activation seam is live
on the default owl + default config. This is the standing guard against the
"registered ≠ reachable / dead-on-default-path" defect class: a subsystem that
ships green but is dead on the path users actually run turns the census RED.
"""

from __future__ import annotations

import pytest

# Importing probes self-registers them into the census registry.
import stackowl.health.reachability.probes  # noqa: F401
from stackowl.health.reachability import (
    REQUIRED_PROBES,
    ProbeResult,
    census_passes,
    reachability_probe,
    registered_probes,
    run_census,
)


async def test_all_subsystems_reachable_on_default_path() -> None:
    """Fail-closed: every registered subsystem is live on the default path."""
    results = await run_census()
    unreachable = [f"{r.name}: {r.detail}" for r in results if not r.reachable]
    assert census_passes(results), f"dead-on-default-path subsystems: {unreachable}"
    assert results, "census ran zero probes — registration is broken"


def test_required_probes_are_registered() -> None:
    """Drift guard: every REQUIRED subsystem has a registered probe (catches
    'added a required default-path seam but forgot its probe')."""
    missing = REQUIRED_PROBES - set(registered_probes())
    assert not missing, f"required subsystems missing a reachability probe: {sorted(missing)}"


async def test_census_is_fail_closed_on_a_dead_subsystem() -> None:
    """Proof the harness catches a dead seam: a deliberately-unreachable probe
    must make the census fail (it cannot pass by omission)."""

    @reachability_probe("test.deliberately_dead")
    async def _dead() -> ProbeResult:  # registered only for this test
        return ProbeResult("test.deliberately_dead", reachable=False, detail="simulated")

    try:
        results = await run_census()
        assert not census_passes(results), "census passed despite a dead subsystem"
    finally:
        registered_probes().pop("test.deliberately_dead", None)  # copy; harmless
        # Remove from the live registry too so other tests are unaffected.
        from stackowl.health.reachability import census as _census

        _census._PROBES.pop("test.deliberately_dead", None)


async def test_census_fail_closes_on_a_raising_probe() -> None:
    """A probe that raises is recorded as unreachable, never silently skipped."""

    @reachability_probe("test.raises")
    async def _boom() -> ProbeResult:
        raise RuntimeError("kaboom")

    try:
        results = await run_census()
        match = [r for r in results if r.name == "test.raises"]
        assert match and not match[0].reachable
        assert "kaboom" in match[0].detail
    finally:
        from stackowl.health.reachability import census as _census

        _census._PROBES.pop("test.raises", None)
