"""S5 — goal→type inference for owl_build.

Unit:  (a) a goal infers preset+specialty; (d) a name is suggested (with reroll).
Gateway: (a) a goal-only create MINTS with zero questions (inference closed the
gap); (b) inference failure FALLS BACK to asking (no crash) and still mints.

The only faked thing is the fast-tier provider; everything else (registry, consent,
clarify gateway, yaml persistence) is real — so removing the inference wiring fails
the gateway journeys.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.meta.owl_build_infer import infer_capability, suggest_display_name
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _Provider:
    """Registry-shaped fast-tier provider returning a fixed completion content."""

    protocol = "anthropic"

    def __init__(self, content: str, model: str = "") -> None:
        self._content = content
        self._model = model
        self.calls = 0
        self.seen_models: list[str] = []

    def get(self, name: str) -> _Provider:
        return self

    def get_by_tier_and_model(self, tier: str) -> tuple[_Provider, str]:
        return self, self._model

    async def complete(self, messages: object, model: str = "", **k: object) -> object:
        self.calls += 1
        self.seen_models.append(model)
        return type("R", (), {"content": self._content})()


# --- (a) unit: goal → preset + specialty ------------------------------------


async def test_a_infer_capability_maps_goal_to_preset() -> None:
    prov = _Provider('{"preset": "analyst", "specialty": "tracks tax filing deadlines"}')
    out = await infer_capability("watch my tax deadlines", prov)  # type: ignore[arg-type]
    assert out == ("analyst", "tracks tax filing deadlines")


async def test_a_infer_capability_rejects_out_of_vocabulary() -> None:
    prov = _Provider('{"preset": "wizard", "specialty": "magic"}')
    assert await infer_capability("do spells", prov) is None  # type: ignore[arg-type]


async def test_b_infer_capability_failopen_on_junk() -> None:
    assert await infer_capability("x", _Provider("sorry, no idea")) is None  # type: ignore[arg-type]


async def test_infer_capability_threads_resolved_model_to_provider_complete() -> None:
    """_complete() must resolve (provider, model) via get_by_tier_and_model and
    pass the SPECIFIC resolved model string into provider.complete(), not the
    old hardcoded model="".
    """
    prov = _Provider(
        '{"preset": "analyst", "specialty": "tracks tax filing deadlines"}',
        model="vendor/fast-tier-model-v3",
    )
    out = await infer_capability("watch my tax deadlines", prov)  # type: ignore[arg-type]
    assert out == ("analyst", "tracks tax filing deadlines")
    assert prov.seen_models == ["vendor/fast-tier-model-v3"], prov.seen_models


# --- (d) unit: name suggestion + reroll -------------------------------------


async def test_d_suggest_display_name_returns_usable_name() -> None:
    name = await suggest_display_name("watch my tax deadlines", _Provider("Tony"))  # type: ignore[arg-type]
    assert name == "Tony"


async def test_d_suggest_display_name_reroll_excludes_avoided() -> None:
    # The "suggest another" capability: an avoided name is rejected → ask instead.
    out = await suggest_display_name(
        "watch taxes", _Provider("Tony"), avoid=("tony",),  # type: ignore[arg-type]
    )
    assert out is None


async def test_suggest_display_name_threads_resolved_model_to_provider_complete() -> None:
    """Same _complete() helper, second entrypoint: the resolved model string
    from get_by_tier_and_model must reach provider.complete() here too.
    """
    prov = _Provider("Tony", model="vendor/fast-tier-model-v3")
    name = await suggest_display_name("watch my tax deadlines", prov)  # type: ignore[arg-type]
    assert name == "Tony"
    assert prov.seen_models == ["vendor/fast-tier-model-v3"], prov.seen_models


# --- gateway scaffolding (mirrors test_owl_build_clarify_gateway) ------------


class _FakeAdapter:
    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.calls: list[tuple[str, str]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_clarify(
        self, session_id: str, question: str, choices: tuple[str, ...], clarify_id: str,
    ) -> None:
        self.calls.append((session_id, question))


_SESSION = "s-owl-infer"


def _services(tmp_db: DbPool, *, registry: OwlRegistry, gateway: ClarifyGateway, prov: _Provider) -> StepServices:
    return StepServices(
        provider_registry=prov,  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=ConsequentialActionGate(ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        clarify_gateway=gateway,
        db_pool=tmp_db,
    )


def _gateway() -> tuple[ClarifyGateway, _FakeAdapter]:
    gw = ClarifyGateway()
    adapter = _FakeAdapter("cli")
    gw.register_adapter("cli", adapter)  # type: ignore[arg-type]
    return gw, adapter


async def _run(services: StepServices, args: dict[str, object]) -> object:
    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_id=_SESSION, trace_id="t-owl-infer", interactive=True,
        channel="cli", delegation_depth=0, owl_name="secretary",
    )
    try:
        return await OwlBuildTool(clarify_timeout_s=5.0).execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)


async def _answer_when_parked(gateway: ClarifyGateway, answer: str) -> None:
    for _ in range(500):
        await asyncio.sleep(0)
        if gateway.peek_for_session(_SESSION, "cli") is not None:
            assert gateway.try_resolve(_SESSION, "cli", answer) is not None
            return
    raise AssertionError("owl_build never parked a clarify question")


# --- (a) gateway: goal-only create mints with ZERO questions -----------------


async def test_a_goal_only_create_mints_without_asking(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = OwlRegistry.with_default_secretary()
    gateway, adapter = _gateway()
    prov = _Provider('{"preset": "analyst", "specialty": "tracks tax deadlines"}')
    services = _services(tmp_db, registry=registry, gateway=gateway, prov=prov)

    # name + goal(specialty) present, NO preset → inference fills it → mint, no ask.
    result = await _run(
        services,
        {"action": "create", "name": "taxbot", "specialty": "watch my tax deadlines"},
    )

    assert result.success, result.error  # type: ignore[attr-defined]
    assert adapter.calls == [], "inference should have closed the gap with no questions"
    assert prov.calls == 1, "exactly one fast-tier inference call expected"
    assert registry.get("taxbot").origin == "agent"


# --- (b) gateway: inference failure FALLS BACK to asking --------------------


async def test_b_inference_failure_falls_back_to_asking(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = OwlRegistry.with_default_secretary()
    gateway, adapter = _gateway()
    prov = _Provider("sorry, I really cannot tell")  # unparseable → infer None
    services = _services(tmp_db, registry=registry, gateway=gateway, prov=prov)

    task = asyncio.ensure_future(
        _run(services, {"action": "create", "name": "helper", "specialty": "watch my tax deadlines"})
    )
    await _answer_when_parked(gateway, "researcher")  # the capability question
    result = await task

    assert result.success, result.error  # type: ignore[attr-defined]
    assert len(adapter.calls) == 1, adapter.calls  # fell back to ONE ask, no crash
    assert registry.get("helper").origin == "agent"
