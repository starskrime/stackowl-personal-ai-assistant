"""Tests for FR-9 sticky routing — triage.py's LLM-router bypass.

The bypass fires iff ALL hold: a StickyRouteCache is wired, the message is
< 200 chars, a fresh (<=5 min) cache entry exists for the session, the cached
owl still resolves in the registry, AND the cached intent_class is
"conversational" (adversarial review, 2026-07-01: a "standard"/work-turn
resolution is the one most likely to be stale by the time a short follow-up
arrives, and reusing it silently defeats the F120 tool-capability gate + the
answer-floor tier — never cached or reused, not just a read-side filter).
Any condition false -> the normal SecretaryRouter path, unchanged.
Direct-address turns never read or write the cache (out of scope for FR-9).
"""

from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.sticky_route_cache import TTL_SECONDS, StickyRouteCache
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import triage as triage_step
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t", session_id="s", input_text="hi", owl_name="secretary",
        channel="cli", pipeline_step="start",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _manifest(name: str, role: str = "generic") -> OwlAgentManifest:
    return OwlAgentManifest(name=name, role=role, system_prompt="Be helpful.", model_tier="fast")


def _make_registry(names: list[str]) -> OwlRegistry:
    registry = OwlRegistry.with_default_secretary()
    for name in names:
        registry.register(_manifest(name))
    return registry


def _build_services(owl_registry: OwlRegistry, mock: MockProvider) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock(mock.name, mock, tier="fast")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        sticky_route_cache=StickyRouteCache(),
    )


# ===========================================================================
# Fresh session: router IS called; a "conversational" result populates the
# cache so the NEXT short follow-up bypasses it.
# ===========================================================================


@pytest.mark.asyncio
async def test_fresh_session_calls_router_then_bypasses_on_followup() -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nconversational")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        first = await triage_step.run(_state(session_id="sess-1", input_text="hey there"))
        assert first.owl_name == "research_owl"
        assert first.intent_class == "conversational"
        assert mock.call_count == 1
        assert services.sticky_route_cache.get("sess-1") == ("research_owl", "conversational")

        second = await triage_step.run(_state(session_id="sess-1", input_text="thanks!"))
        assert mock.call_count == 1  # bypassed — no second router call
        assert second.owl_name == "research_owl"
        assert second.intent_class == "conversational"
        assert second.intent_classified is True
        assert second.clarify_question is None
    finally:
        reset_services(token)


# ===========================================================================
# A "standard" (work-turn) router result is NEVER cached or reused — the
# adversarial-review fix. A short follow-up after a "standard" resolution
# still calls the router (cache stayed empty).
# ===========================================================================


@pytest.mark.asyncio
async def test_standard_result_never_cached_or_reused() -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nstandard")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        first = await triage_step.run(_state(session_id="sess-std", input_text="find me a recipe"))
        assert first.intent_class == "standard"
        assert mock.call_count == 1
        assert services.sticky_route_cache.get("sess-std") is None  # never written

        await triage_step.run(_state(session_id="sess-std", input_text="and another one"))
        assert mock.call_count == 2  # not bypassed — cache was never populated
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_standard_entry_in_cache_is_never_reused_even_if_present() -> None:
    """Defense-in-depth: even a directly-injected "standard" cache entry
    (bypassing the write-side guard) must not be reused on read — belt and
    suspenders per the adversarial review, not just relying on the write
    restriction."""
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nstandard")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        services.sticky_route_cache.set("sess-std2", "research_owl", "standard")
        out = await triage_step.run(_state(session_id="sess-std2", input_text="hi again"))
        assert mock.call_count == 1  # router still called — cached "standard" ignored
        assert out.owl_name == "research_owl"
    finally:
        reset_services(token)


# ===========================================================================
# Message >= 200 chars -> bypass condition fails on length; router IS called.
# ===========================================================================


@pytest.mark.asyncio
async def test_long_followup_bypasses_length_ceiling_calls_router() -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nconversational")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        await triage_step.run(_state(session_id="sess-2", input_text="hey there"))
        assert mock.call_count == 1

        long_text = "x" * 200  # not < 200 chars
        await triage_step.run(_state(session_id="sess-2", input_text=long_text))
        assert mock.call_count == 2
    finally:
        reset_services(token)


# ===========================================================================
# Cache entry beyond TTL -> router IS called (monkeypatch time.monotonic).
# ===========================================================================


@pytest.mark.asyncio
async def test_stale_cache_entry_calls_router(monkeypatch: pytest.MonkeyPatch) -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nconversational")
    services = _build_services(owl_registry, mock)
    token = set_services(services)

    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "stackowl.owls.sticky_route_cache.time.monotonic", lambda: clock["now"]
    )
    try:
        await triage_step.run(_state(session_id="sess-3", input_text="hey there"))
        assert mock.call_count == 1

        clock["now"] += TTL_SECONDS + 1  # advance past the TTL
        await triage_step.run(_state(session_id="sess-3", input_text="thanks!"))
        assert mock.call_count == 2
    finally:
        reset_services(token)


# ===========================================================================
# Cached owl no longer in the registry -> falls through to the router; no crash.
# ===========================================================================


@pytest.mark.asyncio
async def test_cached_owl_removed_falls_through_to_router() -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nconversational")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        services.sticky_route_cache.set("sess-4", "ghost_owl", "conversational")
        out = await triage_step.run(_state(session_id="sess-4", input_text="hi again"))
        assert mock.call_count == 1
        assert out.owl_name == "research_owl"
    finally:
        reset_services(token)


# ===========================================================================
# A "clarify" router result is NEVER cached — a subsequent short follow-up
# still calls the router (cache stayed empty/missed).
# ===========================================================================


@pytest.mark.asyncio
async def test_clarify_result_never_cached() -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="secretary\nclarify\nWhat exactly do you mean?")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        first = await triage_step.run(_state(session_id="sess-5", input_text="huh"))
        assert first.intent_class == "clarify"
        assert mock.call_count == 1
        assert services.sticky_route_cache.get("sess-5") is None

        await triage_step.run(_state(session_id="sess-5", input_text="still confused"))
        assert mock.call_count == 2  # not bypassed — cache was never populated
    finally:
        reset_services(token)


# ===========================================================================
# Direct-address turns never touch the cache — a direct-address turn does not
# seed a sticky bypass for a later secretary-branch turn in the same session.
# ===========================================================================


def test_evict_removes_cached_entry() -> None:
    cache = StickyRouteCache()
    cache.set("sess-evict", "research_owl", "conversational")
    assert cache.get("sess-evict") == ("research_owl", "conversational")

    cache.evict("sess-evict")

    assert cache.get("sess-evict") is None


def test_evict_on_missing_session_is_a_no_op() -> None:
    cache = StickyRouteCache()
    cache.evict("no-such-session")  # must not raise


@pytest.mark.asyncio
async def test_direct_address_never_writes_or_reads_cache() -> None:
    owl_registry = _make_registry(["research_owl"])
    mock = MockProvider(name="router-mock", canned_text="research_owl\nconversational")
    services = _build_services(owl_registry, mock)
    token = set_services(services)
    try:
        direct = await triage_step.run(
            _state(session_id="sess-6", owl_name="research_owl", input_text="@research_owl hi")
        )
        assert direct.owl_name == "research_owl"
        assert mock.call_count == 0  # direct address never calls the router
        assert services.sticky_route_cache.get("sess-6") is None  # nor writes the cache

        followup = await triage_step.run(
            _state(session_id="sess-6", owl_name="secretary", input_text="hi")
        )
        assert mock.call_count == 1  # NOT sticky-bypassed off the direct address
        assert followup.owl_name == "research_owl"
    finally:
        reset_services(token)
