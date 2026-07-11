"""Self-heal degraded boot — all-providers-down must NOT hard-kill the boot.

Before this fix, ``StartupOrchestrator._phase_providers`` let ``ProviderProbe``'s
``StartupError`` propagate, aborting startup entirely: a customer whose only
configured LLM died had no way to reach the chat gateway to self-heal via
``/provider`` (slash commands are LLM-independent, dispatched through
``registry.dispatch()``). Now an all-down probe degrades the boot instead: it
sets ``self._providers_degraded`` (later threaded onto ``StepServices``, which
``_dispatch_turn`` reads to floor LLM-needing turns with a graceful notice while
slash commands keep working).
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.settings import Settings
from stackowl.exceptions import StartupError
from stackowl.pipeline.services import StepServices
from stackowl.startup.orchestrator import StartupOrchestrator


def _dead_provider(name: str) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol="openai",
        base_url="http://localhost:1",
        enabled=True,
        default_model="test-model",
        tier="standard",
    )


def _orch_with_providers(providers: list[ProviderConfig]) -> StartupOrchestrator:
    orch = StartupOrchestrator()
    # Settings kwargs are dropped by settings_customise_sources → use model_copy
    # (same pattern as tests/startup/test_reachability_enforcement.py).
    orch._settings = Settings().model_copy(update={"providers": providers})
    return orch


@pytest.mark.asyncio
async def test_all_down_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every provider unreachable → boot proceeds, flag set, nothing raises."""
    orch = _orch_with_providers([_dead_provider("alpha"), _dead_provider("beta")])

    async def _raise_all_down(self):  # type: ignore[no-untyped-def]
        raise StartupError(4, "providers", "No providers reachable — alpha: unreachable")

    monkeypatch.setattr(
        "stackowl.startup.provider_probe.ProviderProbe.check", _raise_all_down
    )

    assert orch._providers_degraded is False
    await orch._phase_providers()  # must NOT raise
    assert orch._providers_degraded is True


@pytest.mark.asyncio
async def test_healthy_probe_leaves_flag_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """At least one provider reachable → byte-identical (no degradation)."""
    orch = _orch_with_providers([_dead_provider("alpha")])

    async def _ok(self):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr("stackowl.startup.provider_probe.ProviderProbe.check", _ok)

    await orch._phase_providers()
    assert orch._providers_degraded is False


@pytest.mark.asyncio
async def test_no_providers_configured_leaves_flag_false() -> None:
    orch = _orch_with_providers([])
    await orch._phase_providers()
    assert orch._providers_degraded is False


def test_step_services_providers_degraded_defaults_false() -> None:
    assert StepServices().providers_degraded is False
    assert StepServices(providers_degraded=True).providers_degraded is True
