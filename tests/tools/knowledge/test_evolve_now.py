"""Story 3.1 — ``evolve_now`` tool: thin wrapper over ``EvolutionCoordinator.
evolve_one_owl_now`` (AC #1, #2). Mirrors ``reflect_now``'s test structure
(``test_phaseB_self_improvement.py``): missing-deps degrade, exception
degrade, happy path — plus the no-owl-context degrade specific to evolve_now
(it needs ``TraceContext``'s ``owl_name``, unlike reflect_now).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.knowledge.evolve_now import EvolveNowTool


@pytest.fixture(autouse=True)
def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """evolve_one_owl_now's LLM-fallback path calls assert_not_test_mode —
    neutralize as the existing self-improvement tool tests do."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


async def _seed_messages(db: DbPool, owl_name: str, count: int) -> None:
    conv_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (conv_id, f"sess-{owl_name}", owl_name, now, count),
    )
    for i in range(count):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, conv_id, "user", f"sample message {i}", now),
        )


def _wired_registries(owl_name: str) -> tuple[OwlRegistry, ProviderRegistry]:
    owl_registry = OwlRegistry()
    owl_registry.register(
        OwlAgentManifest(
            name=owl_name, role="analyst", system_prompt="Be helpful.",
            model_tier="fast", dna=OwlDNA(curiosity=0.50),
        )
    )
    provider_registry = ProviderRegistry()
    mock = MockProvider(
        name="mock-fast",
        canned_text=(
            '{"challenge_level": 0.0, "verbosity": 0.0, "curiosity": 0.02, '
            '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
        ),
    )
    provider_registry.register_mock("mock-fast", mock, tier="fast")
    return owl_registry, provider_registry


async def test_evolve_now_happy_path_promotes_and_reports_evolved_1(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owl_registry, provider_registry = _wired_registries("nora")
    # The tool constructs EvolutionCoordinator with its default batch_size
    # (10, not overridden) — seed enough excerpts to clear _llm_fallback's
    # "too little material" gate.
    await _seed_messages(tmp_db, "nora", count=10)

    # Story 2.6's gate is real by construction here (the tool never injects a
    # shadow_validator — it constructs EvolutionCoordinator with the same
    # 3-arg shape production uses) and fails CLOSED on cold start (no scored
    # task_outcomes seeded, only messages). Stub the ShadowValidator CLASS the
    # coordinator's __init__ instantiates so this test is about the TOOL
    # wiring, not gate mechanics (see test_evolve_one_owl_now.py for
    # gate-mechanics coverage, which injects the stub directly).
    from stackowl.owls import evolution as evolution_module
    from tests._story_2_6_helpers import AlwaysPassShadowValidator

    monkeypatch.setattr(
        evolution_module, "ShadowValidator",
        lambda db, provider_registry: AlwaysPassShadowValidator(),
    )

    services = StepServices(
        db_pool=tmp_db, provider_registry=provider_registry, owl_registry=owl_registry,
    )
    trace_token = TraceContext.start("sess-1", owl_name="nora")
    services_token = set_services(services)
    try:
        res = await EvolveNowTool().execute()
    finally:
        reset_services(services_token)
        TraceContext.reset(trace_token)

    assert res.success is True, res.error
    assert res.output == "evolved:1"
    # LLM_QUALITY signal strength scales the raw 0.02 delta by 0.3x (0.006).
    assert owl_registry.get("nora").dna.curiosity == pytest.approx(0.506)


async def test_evolve_now_no_material_reports_evolved_0_success_true(
    tmp_db: DbPool,
) -> None:
    """A False (no deltas / gate reject) is a NORMAL outcome — success=True."""
    owl_registry, provider_registry = _wired_registries("freshowl")
    # No messages seeded — _llm_fallback's material gate returns {}.

    services = StepServices(
        db_pool=tmp_db, provider_registry=provider_registry, owl_registry=owl_registry,
    )
    trace_token = TraceContext.start("sess-2", owl_name="freshowl")
    services_token = set_services(services)
    try:
        res = await EvolveNowTool().execute()
    finally:
        reset_services(services_token)
        TraceContext.reset(trace_token)

    assert res.success is True, res.error
    assert res.output == "evolved:0"


async def test_evolve_now_missing_service_degrades_structurally(tmp_db: DbPool) -> None:
    # No provider_registry / owl_registry wired → structured failure, no raise.
    services = StepServices(db_pool=tmp_db)
    trace_token = TraceContext.start("sess-3", owl_name="nora")
    services_token = set_services(services)
    try:
        res = await EvolveNowTool().execute()
    finally:
        reset_services(services_token)
        TraceContext.reset(trace_token)
    assert res.success is False
    assert "evolution subsystem not wired" in (res.error or "")


async def test_evolve_now_no_owl_context_degrades_structurally(tmp_db: DbPool) -> None:
    """Untraced/test context (no owl_name on TraceContext) degrades to a
    structured failure — never raises."""
    owl_registry, provider_registry = _wired_registries("nora")
    services = StepServices(
        db_pool=tmp_db, provider_registry=provider_registry, owl_registry=owl_registry,
    )
    services_token = set_services(services)
    try:
        res = await EvolveNowTool().execute()
    finally:
        reset_services(services_token)
    assert res.success is False
    assert "no owl context" in (res.error or "")


async def test_evolve_now_exception_degrades_structurally(
    tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine coordinator exception is a B5 structured failure, never a raise."""
    owl_registry, provider_registry = _wired_registries("nora")

    from stackowl.owls import evolution as evolution_module

    async def _boom(self, owl_name: str) -> bool:  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(
        evolution_module.EvolutionCoordinator, "evolve_one_owl_now", _boom,
    )

    services = StepServices(
        db_pool=tmp_db, provider_registry=provider_registry, owl_registry=owl_registry,
    )
    trace_token = TraceContext.start("sess-4", owl_name="nora")
    services_token = set_services(services)
    try:
        res = await EvolveNowTool().execute()
    finally:
        reset_services(services_token)
        TraceContext.reset(trace_token)

    assert res.success is False
    assert "evolution failed" in (res.error or "")
    assert "boom" in (res.error or "")


async def test_evolve_now_registered_in_tool_registry() -> None:
    """Registration (Task 3): confirm evolve_now is discoverable the same way
    reflect_now is — registered by default and present in the catalog."""
    from stackowl.tools.registry import ToolRegistry

    registry = ToolRegistry.with_defaults()
    assert registry.get("evolve_now") is not None
