"""ADR-4 — the reachability invariant: a dangling half-edge fails the boot, not the user.

The fail-closed census already runs at boot (warn-only). This proves the enforcement step:
with ``reachability_enforcement="block"`` a registered-but-unreachable capability makes the
boot REFUSE READY (StartupError); with the default ``"warn"`` the boot proceeds (byte-
identical). A broken census auditor never blocks boot in either mode.
"""

from __future__ import annotations

import pytest

from stackowl.config.settings import Settings
from stackowl.exceptions import StartupError
from stackowl.health.reachability import census
from stackowl.health.reachability.census import ProbeResult
from stackowl.startup.orchestrator import StartupOrchestrator

pytestmark = pytest.mark.asyncio


async def _dangling() -> ProbeResult:
    return ProbeResult("test.dangling", reachable=False, detail="deliberately dead edge")


async def _live() -> ProbeResult:
    return ProbeResult("test.live", reachable=True, detail="reached")


def _orch(enforcement: str) -> StartupOrchestrator:
    orch = StartupOrchestrator()
    # Settings kwargs are dropped by settings_customise_sources → use model_copy.
    orch._settings = Settings().model_copy(
        update={"reachability_enforcement": enforcement}
    )
    return orch


@pytest.fixture
def only_dangling(monkeypatch: pytest.MonkeyPatch) -> None:
    # Replace the live probe registry with a single dangling probe (the already-imported
    # probes module won't re-register into this dict). census_passes ⇒ False.
    monkeypatch.setattr(census, "_PROBES", {"test.dangling": _dangling})


@pytest.fixture
def only_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(census, "_PROBES", {"test.live": _live})


# --- the invariant: block mode refuses READY on a dangling capability ----------


async def test_block_mode_refuses_ready_on_dangling_edge(only_dangling: None) -> None:
    orch = _orch("block")
    with pytest.raises(StartupError) as exc:
        await orch._phase_reachability_census()
    assert exc.value.name == "reachability"


async def test_block_mode_passes_when_all_reachable(only_live: None) -> None:
    orch = _orch("block")
    # No raise — a fully-reachable census readies normally even in block mode.
    await orch._phase_reachability_census()


# --- warn mode (default) is byte-identical: never raises -----------------------


async def test_warn_mode_does_not_block_on_dangling_edge(only_dangling: None) -> None:
    orch = _orch("warn")
    # Loud alert, but the boot proceeds — the historical advisory behavior.
    await orch._phase_reachability_census()


async def test_default_enforcement_is_warn() -> None:
    assert Settings().reachability_enforcement == "warn"


# --- a broken auditor never blocks boot, even in block mode --------------------


async def test_broken_census_never_blocks_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the AUDITOR itself failing (distinct from a probe verdict of unreachable).
    # The method imports run_census from the package at call-time, so patch it there.
    async def _raising_run_census() -> list[ProbeResult]:
        raise RuntimeError("census machinery broke")

    import stackowl.health.reachability as reach_pkg

    monkeypatch.setattr(reach_pkg, "run_census", _raising_run_census)
    orch = _orch("block")
    # The auditor blowing up must NOT brick the boot (advisory failure), even in block.
    await orch._phase_reachability_census()
